import sys
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector


PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import phantom_storyboard
from phantom_storyboard.operators import (
    BAKE,
    CAGE,
    KIND,
    ROOT,
    SCREEN,
    _camera_floor_position,
)


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

    camera_data = bpy.data.cameras.new("SmokeCamera")
    camera = bpy.data.objects.new("SmokeCamera", camera_data)
    scene.collection.objects.link(camera)
    camera.location = (0.0, -10.0, 0.0)
    camera.rotation_euler.x = 1.5707963267948966
    scene.camera = camera

    pitched_data = bpy.data.cameras.new("PitchedCamera")
    pitched_camera = bpy.data.objects.new("PitchedCamera", pitched_data)
    scene.collection.objects.link(pitched_camera)
    pitched_camera.location = (7.0, -7.0, 5.0)
    pitched_camera.rotation_euler = (
        Vector((0.0, 0.0, 0.0)) - pitched_camera.location
    ).to_track_quat("-Z", "Y").to_euler()
    scene.camera = pitched_camera
    bpy.context.view_layer.update()
    pitched_floor = _camera_floor_position(scene, Vector((0.0, 0.0, 0.0)), -1.0)
    pitched_ndc = world_to_camera_view(scene, pitched_camera, pitched_floor)
    assert abs(pitched_ndc.y) < 0.000001, (pitched_ndc[:], pitched_floor[:])
    scene.camera = camera
    bpy.data.objects.remove(pitched_camera, do_unlink=True)
    bpy.data.cameras.remove(pitched_data)

    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "SmokeCharacter"
    cube.hide_render = False

    assert bpy.ops.phantom.create() == {"FINISHED"}
    root = one_object(ROOT)
    cage = one_object(CAGE)
    assert root.get("is_phantom") is True
    bounds = root.constraints.get("PhantomBounds")
    assert bounds is not None
    assert bounds.use_min_z is True and bounds.use_max_z is False
    assert not any((bounds.use_min_x, bounds.use_max_x, bounds.use_min_y, bounds.use_max_y))
    assert cage.hide_render is True
    assert cube.parent == root
    floor = float(root["phantom_floor_z"])
    assert round(root.location.z, 6) == round(floor, 6)
    assert round(cage.location.z - cage.dimensions.z / 2.0, 6) == round(floor, 6)
    floor_ndc = world_to_camera_view(scene, camera, Vector(root.phantom_box_position))
    assert abs(floor_ndc.y) < 0.000001
    cube_floor = min((cube.matrix_world @ Vector(corner)).z for corner in cube.bound_box)
    assert round(cube_floor, 6) == round(floor, 6), (cube_floor, floor, root.location[:])

    root.location.x = 100.0
    root.location.z = floor - 10.0
    bpy.context.view_layer.update()
    evaluated_root = root.evaluated_get(bpy.context.evaluated_depsgraph_get())
    assert evaluated_root.matrix_world.translation.x > 99.0
    assert round(evaluated_root.matrix_world.translation.z, 6) == round(floor, 6)
    root.location.x = 0.0
    root.location.z = floor

    root.phantom_box_position.z += 2.0
    active(root)
    assert bpy.ops.phantom.update_box() == {"FINISHED"}
    floor += 2.0
    assert round(cage.location.z - cage.dimensions.z / 2.0, 6) == round(floor, 6)
    bpy.context.view_layer.update()
    evaluated_root = root.evaluated_get(bpy.context.evaluated_depsgraph_get())
    assert round(evaluated_root.matrix_world.translation.z, 6) == round(floor, 6)

    scene.phantom_settings.box_size = (4.0, 5.0, 6.0)
    active(root)
    assert bpy.ops.phantom.update_box() == {"FINISHED"}
    assert tuple(round(value, 4) for value in cage.dimensions) == (4.0, 5.0, 6.0)

    bpy.ops.mesh.primitive_uv_sphere_add(location=(1.0, 0.0, 10.0))
    accessory = bpy.context.active_object
    accessory.keyframe_insert(data_path="location", frame=1)
    accessory.location.z = 11.0
    accessory.keyframe_insert(data_path="location", frame=2)
    scene.frame_set(1)
    root.select_set(True)
    bpy.context.view_layer.objects.active = root
    assert bpy.ops.phantom.attach_selected() == {"FINISHED"}
    assert accessory.parent == root
    accessory_floor = min(
        (accessory.matrix_world @ Vector(corner)).z for corner in accessory.bound_box
    )
    assert abs(accessory_floor - floor) < 0.00001
    scene.frame_set(2)
    animated_floor = min(
        (accessory.matrix_world @ Vector(corner)).z for corner in accessory.bound_box
    )
    assert abs(animated_floor - (floor + 1.0)) < 0.00001
    scene.frame_set(1)

    stroke_data = bpy.data.grease_pencils.new("SmokeStrokeData")
    stroke = bpy.data.objects.new("Stroke.001", stroke_data)
    scene.collection.objects.link(stroke)
    stroke.parent = root
    stroke.matrix_parent_inverse = root.matrix_world.inverted()
    try:
        bpy.ops.phantom.import_model(
            filepath="/tmp/phantom_missing_model.obj", target_name=root.name
        )
    except RuntimeError as exc:
        assert "3D model file does not exist" in str(exc)
    else:
        raise AssertionError("Missing model import should fail")
    assert stroke.parent == root

    model_path = Path("/tmp/phantom_loaded_model.obj")
    model_path.write_text(
        "o LoadedModel\nv -1 -1 0\nv 1 -1 0\nv 0 1 2\nf 1 2 3\n"
    )
    assert bpy.ops.phantom.import_model(
        filepath=str(model_path), target_name=root.name
    ) == {"FINISHED"}
    loaded = [
        obj
        for obj in bpy.data.objects
        if obj.get("phantom_character_root") and obj not in {cube, accessory}
    ]
    assert len(loaded) == 1
    assert loaded[0].parent == root
    assert loaded[0].users_collection == (scene.phantom_settings.phantom_collection,)
    assert stroke.parent is None

    root.location.x += 50.0
    bpy.context.view_layer.update()
    active(root)
    assert bpy.ops.phantom.create_screen() == {"FINISHED"}
    screen = one_object(SCREEN)
    assert screen.data.uv_layers.active is not None
    assert any(child.type == "LIGHT" for child in screen.children)
    assert screen["phantom_cage_owner_id"] == root["phantom_id"]
    camera_inverse = camera.matrix_world.inverted()
    screen_camera = camera_inverse @ screen.matrix_world.translation
    cage_camera = camera_inverse @ cage.matrix_world.translation
    cage_corner_depths = [
        (camera_inverse @ (cage.matrix_world @ Vector(corner))).z
        for corner in cage.bound_box
    ]
    assert abs(screen_camera.x - cage_camera.x) < 0.00001
    assert abs(screen_camera.y - cage_camera.y) < 0.00001
    assert abs(
        screen_camera.z
        - (min(cage_corner_depths) - scene.phantom_settings.screen_distance)
    ) < 0.00001
    screen_depths = [vertex.co.y for vertex in screen.data.vertices]
    assert min(screen_depths) < 0.0
    assert round(max(screen_depths), 6) == 0.0

    old_screen_location = screen.location.copy()
    root.phantom_box_position.x += 1.0
    active(root)
    assert bpy.ops.phantom.update_box() == {"FINISHED"}
    assert (screen.location - old_screen_location).length > 0.5
    root.location.x -= 50.0
    bpy.context.view_layer.update()

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

    legacy = bpy.data.objects.new("LegacyPhantom", None)
    scene.collection.objects.link(legacy)
    legacy[KIND] = ROOT
    legacy["is_phantom"] = True
    legacy["phantom_box_center"] = (0.0, 0.0, 2.5)
    legacy["phantom_box_size"] = (4.0, 4.0, 5.0)
    legacy.location.z = 2.5
    legacy.keyframe_insert(data_path="location", frame=1)
    legacy.location.z = 3.5
    legacy.keyframe_insert(data_path="location", frame=2)
    active(legacy)
    assert bpy.ops.phantom.update_box() == {"FINISHED"}
    scene.frame_set(1)
    bpy.context.view_layer.update()
    evaluated_legacy = legacy.evaluated_get(bpy.context.evaluated_depsgraph_get())
    assert round(evaluated_legacy.matrix_world.translation.z, 6) == 0.0
    scene.frame_set(2)
    bpy.context.view_layer.update()
    evaluated_legacy = legacy.evaluated_get(bpy.context.evaluated_depsgraph_get())
    assert round(evaluated_legacy.matrix_world.translation.z, 6) == 1.0
    print("PHANTOM_SMOKE_OK")


if __name__ == "__main__":
    main()
