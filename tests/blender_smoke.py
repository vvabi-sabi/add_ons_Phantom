import sys
from pathlib import Path

import bpy


PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import phantom_storyboard
from phantom_storyboard.operators import BAKE, CAGE, KIND, ROOT, SCREEN


def active(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def one_object(kind):
    objects = [obj for obj in bpy.data.objects if obj.get(KIND) == kind]
    assert len(objects) == 1, f"Expected one {kind}, got {len(objects)}"
    return objects[0]


def main():
    phantom_storyboard.register()
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 32
    scene.render.resolution_y = 32
    scene.render.resolution_percentage = 100
    scene.frame_start = 1
    scene.frame_end = 2
    scene.phantom_settings.bake_step = "2"

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "SmokeCharacter"
    cube.hide_render = False

    assert bpy.ops.phantom.create() == {"FINISHED"}
    root = one_object(ROOT)
    cage = one_object(CAGE)
    assert root.get("is_phantom") is True
    assert root.constraints.get("PhantomBounds") is not None
    assert cage.hide_render is True
    assert cube.parent == root

    scene.phantom_settings.box_size = (4.0, 5.0, 6.0)
    active(root)
    assert bpy.ops.phantom.update_box() == {"FINISHED"}
    assert tuple(round(value, 4) for value in cage.dimensions) == (4.0, 5.0, 6.0)

    assert bpy.ops.phantom.create_screen() == {"FINISHED"}
    screen = one_object(SCREEN)
    assert screen.data.uv_layers.active is not None
    assert any(child.type == "LIGHT" for child in screen.children)

    camera_data = bpy.data.cameras.new("SmokeCamera")
    camera = bpy.data.objects.new("SmokeCamera", camera_data)
    scene.collection.objects.link(camera)
    camera.location = (0.0, -10.0, 0.0)
    camera.rotation_euler.x = 1.5707963267948966
    scene.camera = camera

    active(root)
    smoke_file = Path("/tmp/phantom_storyboard_smoke.blend")
    bpy.ops.wm.save_as_mainfile(filepath=str(smoke_file))
    assert bpy.ops.phantom.bake() == {"FINISHED"}
    projection = one_object(BAKE)
    assert projection.hide_render is False
    assert cube.hide_render is True

    assert bpy.ops.phantom.use_bake(use_bake=False) == {"FINISHED"}
    assert cube.hide_render is False
    assert projection.hide_render is True
    assert bpy.ops.phantom.use_bake(use_bake=True) == {"FINISHED"}
    assert cube.hide_render is True
    assert projection.hide_render is False

    output_dir = Path(bpy.path.abspath(f"//phantom_bakes/Phantom_{root['phantom_id'][:8]}"))
    assert (output_dir / "phantom_000001.png").is_file()
    assert (output_dir / "phantom_000002.png").is_file()
    print("PHANTOM_SMOKE_OK")


if __name__ == "__main__":
    main()
