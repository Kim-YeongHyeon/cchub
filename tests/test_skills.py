import subprocess
from pathlib import Path

import pytest

from cchub import skills
from cchub.ssh import RunResult
from conftest import FakeRemote

FIXTURE_SKILL = Path(__file__).parent / "fixtures" / "sample_skill"

requires_smoke_ssh = pytest.mark.skipif(
    subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=2",
                    "cchub-smoke", "true"], capture_output=True).returncode != 0,
    reason="cchub-smoke ssh alias 불가",
)

SCAN_OUT = (
    "personal\t/home/u/.claude/skills/my-skill/SKILL.md\t개인 스킬 설명\n"
    "project\t/home/u/proj/.claude/skills/e2e-run/SKILL.md\tE2E 실행 스킬\n"
    "project\t/home/u/proj/.claude/skills/no-desc/SKILL.md\t\n"
    "이상한 줄은 무시\n"
)


def test_valid_skill_name():
    assert skills.valid_skill_name("e2e-run")
    assert skills.valid_skill_name("My_Skill2")
    for bad in ("", "a/b", "..", "a b", "a;rm", "한글"):
        assert not skills.valid_skill_name(bad)


def test_scan_skills_parses_output():
    fake = FakeRemote({"sh": RunResult(0, SCAN_OUT, "")})
    out = skills.scan_skills(fake, "srv1", ["/home/u/proj"], ["~/extra"])
    assert [s.name for s in out] == ["my-skill", "e2e-run", "no-desc"]
    assert out[0].scope == "personal" and out[0].server == "srv1"
    assert out[0].path == "/home/u/.claude/skills/my-skill"
    assert out[1].scope == "project" and out[1].description == "E2E 실행 스킬"
    assert out[2].description == ""
    # 스크립트에 세 루트가 전부 들어감 ($HOME 확장형 + 인용된 절대경로)
    script = fake.calls[0][2]
    assert '"$HOME"/.claude/skills' in script
    assert "/home/u/proj/.claude/skills" in script
    assert '"$HOME"/extra/.claude/skills' in script


def test_scan_skills_failure_returns_empty():
    fake = FakeRemote({"sh": RunResult(255, "", "down")})
    assert skills.scan_skills(fake, "srv1", [], []) == []


def test_scan_skills_dedupes_cwds():
    fake = FakeRemote({"sh": RunResult(0, "", "")})
    skills.scan_skills(fake, "srv1", ["/a", "/a", "/b"], [])
    script = fake.calls[0][2]
    assert script.count("/a/.claude/skills") == 1


def test_local_skills(tmp_path):
    lib = tmp_path / "skills"
    (lib / "sample-skill").mkdir(parents=True)
    import shutil
    shutil.copy(FIXTURE_SKILL / "SKILL.md", lib / "sample-skill" / "SKILL.md")
    (lib / "not-a-skill").mkdir()  # SKILL.md 없음 → 제외
    out = skills.local_skills(lib)
    assert len(out) == 1
    s = out[0]
    assert (s.server, s.name, s.scope) == ("local", "sample-skill", "local")
    assert "샘플 스킬" in s.description
    assert skills.local_skills(tmp_path / "없음") == []


def test_scan_skills_description_with_tabs_survives():
    out = "project\t/home/u/p/.claude/skills/tabby/SKILL.md\t탭이\t들어간\t설명\n"
    fake = FakeRemote({"sh": RunResult(0, out, "")})
    result = skills.scan_skills(fake, "srv1", [], [])
    assert len(result) == 1
    assert result[0].name == "tabby"
    assert result[0].description == "탭이\t들어간\t설명"


def make_lib(tmp_path):
    lib = tmp_path / "skills"
    (lib / "my-skill").mkdir(parents=True)
    import shutil
    shutil.copy(FIXTURE_SKILL / "SKILL.md", lib / "my-skill" / "SKILL.md")
    return lib


def test_pull_skill_fetches_into_lib(tmp_path):
    fake = FakeRemote()
    info = skills.SkillInfo(server="srv1", name="e2e-run", scope="project",
                            path="/home/u/proj/.claude/skills/e2e-run", description="")
    r = skills.pull_skill(fake, info, tmp_path / "skills")
    assert r.rc == 0
    assert fake.fetches == [("/home/u/proj/.claude/skills/e2e-run", tmp_path / "skills")]


def test_pull_skill_rejects_bad_name(tmp_path):
    fake = FakeRemote()
    info = skills.SkillInfo(server="s", name="../evil", scope="personal",
                            path="/x/../evil", description="")
    r = skills.pull_skill(fake, info, tmp_path)
    assert r.rc != 0 and fake.fetches == []


def test_deploy_skill_mkdirs_then_pushes(tmp_path):
    fake = FakeRemote()
    lib = make_lib(tmp_path)
    r = skills.deploy_skill(fake, lib, "my-skill")
    assert r.rc == 0
    assert fake.calls[0] == ["mkdir", "-p", ".claude/skills/my-skill"]
    assert fake.pushes == [(lib / "my-skill", "~/.claude/skills/my-skill")]


def test_deploy_skill_requires_local_skill(tmp_path):
    fake = FakeRemote()
    r = skills.deploy_skill(fake, tmp_path, "없는스킬")   # 이름 검증도 실패
    assert r.rc != 0 and fake.pushes == []
    r2 = skills.deploy_skill(fake, tmp_path, "ghost")     # 이름은 유효, 디렉토리 없음
    assert r2.rc != 0 and fake.pushes == []


def test_delete_skill_uses_fixed_prefix(tmp_path):
    fake = FakeRemote()
    r = skills.delete_skill(fake, "my-skill")
    assert r.rc == 0
    assert fake.calls[0] == ["rm", "-rf", ".claude/skills/my-skill"]


def test_delete_skill_rejects_bad_name():
    fake = FakeRemote()
    r = skills.delete_skill(fake, "../../etc")
    assert r.rc != 0 and fake.calls == []


@requires_smoke_ssh
def test_real_scan_and_deploy_roundtrip(tmp_path):
    """실제 SSH로 스캔 + 스크래치 스킬 deploy→재스캔→delete 왕복 (실제 스킬은 불변)."""
    from cchub.ssh import SSHRemote

    remote = SSHRemote("cchub-smoke")
    # 1) 프로젝트 스킬 스캔 (skill_paths로 envector-msa 지정 — pane cwd 무관하게 보장)
    found = skills.scan_skills(remote, "local", [], ["~/envector-msa"])
    assert any(s.name == "e2e-run" and s.scope == "project" for s in found)
    assert all(s.description for s in found if s.name == "e2e-run")

    # 2) 스크래치 스킬 왕복 (고유 이름으로 실제 개인 스킬과 충돌 방지)
    name = "cchub-smoke-roundtrip"
    lib = tmp_path / "lib"
    (lib / name).mkdir(parents=True)
    (lib / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: cchub M4 스모크 테스트용 임시 스킬\n---\n")
    try:
        assert skills.deploy_skill(remote, lib, name).rc == 0
        found = skills.scan_skills(remote, "local", [], [])
        assert any(s.name == name and s.scope == "personal" for s in found)
    finally:
        assert skills.delete_skill(remote, name).rc == 0
    found = skills.scan_skills(remote, "local", [], [])
    assert not any(s.name == name for s in found)
