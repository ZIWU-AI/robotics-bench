"""Convert the reference task left/right LEAP URDF subtrees to MuJoCo."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET
import mujoco

HERE = Path(__file__).resolve().parent

def main(source):
    destination = HERE / "assets/menagerie/leap_reference"
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for side, suffix in [("left", "_left"), ("right", "")]:
        path = source / f"maniladder/assets/urdf/panda_leaphand/panda_leaphand{suffix}.urdf"
        root = ET.parse(path).getroot()
        arm_links = {f"link{i}" for i in range(8)}
        for node in list(root):
            if node.tag == "link" and node.get("name") in arm_links:
                root.remove(node)
            elif node.tag == "joint" and node.find("parent").get("link") in arm_links:
                root.remove(node)
        ext = ET.SubElement(root, "mujoco")
        ET.SubElement(ext, "compiler", discardvisual="false", fusestatic="false", strippath="false")
        for mesh in root.findall(".//mesh"):
            src = (path.parent / mesh.get("filename")).resolve()
            relative = Path("assets") / side / src.parent.name / src.name
            dst = destination / relative
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            mesh.set("filename", relative.as_posix())
            manifest[dst.relative_to(HERE).as_posix()] = hashlib.sha256(dst.read_bytes()).hexdigest()
        urdf = destination / f"{side}.urdf"
        ET.ElementTree(root).write(urdf)
        model = mujoco.MjModel.from_xml_path(str(urdf))
        output = destination / f"{side}_hand.xml"
        mujoco.mj_saveLastXML(str(output), model)
        xml = ET.parse(output)
        tree = xml.getroot()
        tree.find("compiler").set("meshdir", ".")
        ET.SubElement(tree, "default")
        for index, geom in enumerate(tree.findall(".//geom")):
            visual = geom.get("contype") == "0"
            geom.set("group", "2" if visual else "3")
            geom.set("name", f"geom_{index}")
            if visual:
                geom.set("rgba", "0.85 0.85 0.85 1" if "tip" in geom.get("mesh", "") else "0.12 0.12 0.14 1")
        actuator = ET.SubElement(tree, "actuator")
        for joint in tree.findall(".//joint"):
            if joint.get("name") is not None:
                ET.SubElement(actuator, "position", name=joint.get("name"), joint=joint.get("name"),
                              kp="400", kv="10", forcerange="-50 50", ctrlrange=joint.get("range"))
        ET.SubElement(tree, "contact")
        ET.SubElement(tree.find("worldbody"), "site", name="wrist_site", pos="0 0 0")
        xml.write(output)
        print(side, model.nq, model.ngeom)
    for src, name in [(source/"LICENSE", "ManiLadder-MIT.txt"),
                      (source/"maniladder/assets/hands/leap_hand/LICENSE.txt", "LEAP-MIT.txt")]:
        shutil.copyfile(src, HERE/"licenses"/name)
    (HERE/"leap-provenance.json").write_text(json.dumps({
        "source": "https://github.com/HeegerGao/ManiLadder-Benchmark",
        "commit": subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip(),
        "conversion": "prepare_leap.py; hand subtree with original visual/collision geometry and joint transforms",
        "files": manifest}, indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    main(parser.parse_args().source)
