import concurrent.futures
import json
import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install_skill.py"
CORE = ROOT / "plugins/moct/skills/moct"


class ClientTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name)

    def install(self, target, check=True):
        return subprocess.run([sys.executable, "-B", str(INSTALL), target, "--project", str(self.project)],
                              capture_output=True, text=True, timeout=10, check=check)

    def test_client_discovery_paths_and_complete_bundles(self):
        for target, folder in (("deepseek", ".dsh"), ("kimi", ".kimi-code")):
            self.install(target)
            installed = self.project / folder / "skills/moct"
            self.assertTrue((installed / "SKILL.md").is_file())
            self.assertEqual((installed / "SKILL.md").read_bytes(), (ROOT / "clients/moct/SKILL.md").read_bytes())
            for resource in ("references/moct.md", "references/implementation-handoff.md", "scripts/lessons.py"):
                self.assertEqual((installed / resource).read_bytes(), (CORE / resource).read_bytes())
                self.assertEqual((ROOT / "clients/moct" / resource).read_bytes(), (CORE / resource).read_bytes())
            self.assertIn("already installed", self.install(target).stdout)
        self.assertFalse((self.project / ".agents").exists())

    def test_user_edits_and_unrelated_files_survive_a_retry(self):
        self.install("kimi")
        entry = self.project / ".kimi-code/skills/moct/SKILL.md"
        entry.write_text("my version", encoding="utf-8")
        extra = entry.parent / "notes.txt"
        extra.write_text("my notes", encoding="utf-8")
        self.assertNotEqual(self.install("kimi", check=False).returncode, 0)
        self.assertEqual(entry.read_text(), "my version")
        self.assertEqual(extra.read_text(), "my notes")

    def test_concurrent_install_has_one_complete_result(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.install, ("deepseek", "deepseek")))
        self.assertEqual(sorted("already installed" in result.stdout for result in results), [False, True])
        self.assertTrue((self.project / ".dsh/skills/moct/scripts/lessons.py").is_file())

    def test_unlisted_target_is_rejected_before_writing(self):
        self.assertNotEqual(self.install("unlisted", check=False).returncode, 0)
        self.assertEqual(list(self.project.iterdir()), [])

    def notes(self, script, db, args, note=None, check=True):
        result = subprocess.run([sys.executable, "-B", str(script), "--db", str(db), *args],
                                input=json.dumps(note) if note is not None else None,
                                capture_output=True, text=True, timeout=10, check=check)
        return json.loads(result.stdout) if result.returncode == 0 else result.returncode

    def test_review_crosses_clients_and_forget_is_visible_in_codex(self):
        self.install("deepseek")
        self.install("kimi")
        deepseek = self.project / ".dsh/skills/moct/scripts/lessons.py"
        kimi = self.project / ".kimi-code/skills/moct/scripts/lessons.py"
        codex = CORE / "scripts/lessons.py"
        db = self.project / "user data" / "feedback.sqlite3"
        self.assertEqual(self.notes(codex, db, ["list"]), [])
        self.assertFalse(db.parent.exists())
        note = {"when": "У данных бывает пропуск и числовой ноль", "check": "Проверь отдельно 0 и null."}
        saved = self.notes(deepseek, db, ["save", "--scope", "data"], note)
        self.assertEqual(self.notes(kimi, db, ["list", "--scope", "data"]), [saved])
        self.assertEqual(self.notes(kimi, db, ["list", "--scope", "writing"]), [])
        self.assertEqual(self.notes(kimi, db, ["save", "--scope", "data"], note), saved)
        self.assertEqual(len(self.notes(codex, db, ["list"])), 1)
        self.assertTrue(self.notes(kimi, db, ["forget", saved["id"]])["removed"])
        self.assertFalse(self.notes(kimi, db, ["forget", saved["id"]])["removed"])
        self.assertEqual(self.notes(codex, db, ["list"]), [])

    def test_save_is_atomic_scoped_and_rejects_extra_content(self):
        script = CORE / "scripts/lessons.py"
        db = self.project / "profile.sqlite3"
        note = {"when": "Повтор", "check": "Проверить дважды"}
        self.assertNotEqual(self.notes(script, db, ["save", "--scope", "data"],
                                       dict(note, transcript="extra"), check=False), 0)
        self.assertFalse(db.exists())
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            replies = list(pool.map(lambda _: self.notes(script, db, ["save", "--scope", "general"], note),
                                    range(3)))
        self.assertEqual(len({row["id"] for row in replies}), 1)
        self.assertEqual(self.notes(script, db, ["list", "--scope", "interface"]), [replies[0]])


class MigrationTests(unittest.TestCase):
    def test_legacy_profile_migrates_once_preserving_ids_and_backup(self):
        spec = importlib.util.spec_from_file_location("moct_lessons", CORE / "scripts/lessons.py")
        lessons = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lessons)
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            legacy = home / ".most/lessons.sqlite3"
            legacy.parent.mkdir()
            with closing(sqlite3.connect(legacy)) as db, db:
                db.execute("CREATE TABLE most_lessons (id TEXT PRIMARY KEY, scope TEXT, applies TEXT, criterion TEXT)")
                db.execute("INSERT INTO most_lessons VALUES ('kept-id', 'data', 'zero or unknown', 'check both')")
            original = legacy.read_bytes()
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                paths = list(pool.map(lessons.default_db, (home, home)))
            self.assertEqual(paths, [home / ".moct/lessons.sqlite3"] * 2)
            self.assertEqual(lessons.list_notes(paths[0])[0]["id"], "kept-id")
            self.assertEqual(legacy.read_bytes(), original)
            self.assertTrue(lessons.forget(paths[0], "kept-id"))
            self.assertEqual(lessons.list_notes(lessons.default_db(home)), [])
            self.assertEqual(legacy.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
