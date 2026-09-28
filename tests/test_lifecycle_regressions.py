"""Isolated regression checks for preservation, revocation, and path confinement."""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS=Path(__file__).resolve().parents[1]/"scripts"
sys.path.insert(0,str(SCRIPTS))
from _common import atomic_json, read_json, sha256
import project as lifecycle


class LifecycleRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.workspace=self.root/"workspace"
        self.project=self.workspace/"projects/fixture"
        with redirect_stdout(io.StringIO()):
            lifecycle.init(argparse.Namespace(workspace=str(self.workspace),id="fixture",mode="create",ref=None))
        cfg=read_json(self.project/"project.json")
        cfg.update(output={"width":32,"height":24,"fps":{"num":24,"den":1},"frames":2,"colorPolicy":"srgb-to-bt709-limited"},renderEntry="src/main.js",scoreEntry="audio/score.py")
        atomic_json(self.project/"project.json",cfg)
        (self.project/"src").mkdir()
        (self.project/"audio").mkdir()
        (self.project/"src/main.js").write_text("export async function prepare(){}; export function drawFrame(ctx,env){}\n")
        (self.project/"audio/score.py").write_text("def compose(s,t,p):\n return {'silence':s.track()}\n")
        for name in ("BRIEF.md","SPEC.md"):
            (self.project/name).write_text("Controlled safety regression only. No real creative or user feedback claim.\n")
        atomic_json(self.project/"timeline.json",{"shots":[{"id":"s","f0":0,"f1":2}],"events":[]})

    def snapshot(self,revision):
        with redirect_stdout(io.StringIO()):
            lifecycle.snapshot(argparse.Namespace(project=str(self.project),revision=revision))

    def forget(self,delete=False):
        result=subprocess.run([sys.executable,str(SCRIPTS/"project.py"),"forget","--workspace",str(self.workspace),"--id","parent"]+(["--delete"] if delete else []),text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def preferences(self):
        instruction=self.workspace/"instruction.txt"
        instruction.write_text("SYNTHETIC TEST FIXTURE. Test-only scoped instruction, not user feedback.\n")
        base={"category":"aesthetic","rule":"CONTROLLED_LEARNING_RECORD_DELETE_ME","scope":{"type":"project","id":"fixture"},"appliesWhen":"isolated regression only","status":"confirmed",
              "sources":[{"path":"instruction.txt","sha256":sha256(instruction),"explicitConfirmation":True}],"updatedAt":"2026-09-28"}
        rows=[dict(base,id="parent",derivedFrom=[]),dict(base,id="child",derivedFrom=["parent"]),dict(base,id="grandchild",derivedFrom=["child"])]
        atomic_json(self.workspace/"memory/preferences.json",rows)
        return rows

    def test_restore_collision_does_not_mutate_working_copy(self):
        self.snapshot("r001")
        self.snapshot("r002")
        working=self.project/"src/main.js"
        working.write_text("UNCOMMITTED WORK MUST SURVIVE FAILED RESTORE\n")
        before_config=sha256(self.project/"project.json")
        with self.assertRaises(ValueError):
            lifecycle.restore(argparse.Namespace(project=str(self.project),from_revision="r001",revision="r002"))
        self.assertEqual(working.read_text(),"UNCOMMITTED WORK MUST SURVIVE FAILED RESTORE\n")
        self.assertEqual(sha256(self.project/"project.json"),before_config)
        self.assertFalse((self.project/"history/working-before-restore-r002").exists())

    def test_workspace_memory_symlink_cannot_cross_boundary(self):
        outside=self.root/"other-workspace-memory"
        shutil.copytree(self.workspace/"memory",outside)
        shutil.rmtree(self.workspace/"memory")
        (self.workspace/"memory").symlink_to(outside,target_is_directory=True)
        before=sha256(outside/"preferences.json")
        result=subprocess.run([sys.executable,str(SCRIPTS/"project.py"),"memory","--project",str(self.project)],text=True,capture_output=True)
        self.assertNotEqual(result.returncode,0,"Escaping memory symlink was accepted")
        self.assertIn("escap",result.stderr.lower())
        self.assertEqual(sha256(outside/"preferences.json"),before)

    def test_delete_after_retract_upgrades_all_tombstones(self):
        rows=self.preferences()
        self.forget()
        self.forget(delete=True)
        tombstones={r["id"]:r for r in read_json(self.workspace/"memory/tombstones.json")}
        for record in rows:
            self.assertIn(record["id"],tombstones)
            self.assertTrue(tombstones[record["id"]]["deleted"])
            with self.assertRaises(ValueError):
                lifecycle.validate_memory(self.workspace,"preferences",[record])
        self.assertEqual(read_json(self.workspace/"memory/preferences.json"),[])

    def test_delete_scrubs_copied_learning_in_internal_restore_backups(self):
        self.preferences()
        self.snapshot("r001")
        atomic_json(self.project/"used-memory.json",[{"id":"parent","purpose":"COPIED_PARENT_DELETE_ME"},{"id":"child","purpose":"COPIED_CHILD_DELETE_ME"}])
        with redirect_stdout(io.StringIO()):
            lifecycle.restore(argparse.Namespace(project=str(self.project),from_revision="r001",revision="r002"))
        backup=self.project/"history/working-before-restore-r002/used-memory.json"
        self.assertIn("COPIED_PARENT_DELETE_ME",backup.read_text())
        self.forget(delete=True)
        for path in self.project.rglob("used-memory.json"):
            contents=path.read_text()
            self.assertNotIn("COPIED_PARENT_DELETE_ME",contents,str(path))
            self.assertNotIn("COPIED_CHILD_DELETE_ME",contents,str(path))
            self.assertNotIn("CONTROLLED_LEARNING_RECORD_DELETE_ME",contents,str(path))
        # Source instruction is a separately scoped input, not a copied learned summary.
        self.assertTrue((self.workspace/"instruction.txt").exists())

    @unittest.skipUnless(shutil.which("node"),"Node required")
    def test_renderer_denies_output_symlink_before_launch_or_write(self):
        self.snapshot("r001")
        outside=self.root/"outside-output"
        outside.mkdir()
        (self.project/"out").symlink_to(outside,target_is_directory=True)
        result=subprocess.run(["node",str(SCRIPTS/"render.mjs"),"--project",str(self.project),"--revision","r001","stills","0"],text=True,capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("escap",result.stderr.lower())
        self.assertEqual(list(outside.iterdir()),[],"Renderer wrote outside project before failing")


if __name__=="__main__":
    unittest.main()
