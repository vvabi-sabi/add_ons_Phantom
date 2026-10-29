import math
import re
import shutil
import uuid
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ImportHelper
from mathutils import Matrix, Quaternion, Vector

from .geometry import curved_screen_geometry, held_frame_groups, unit_cube_geometry


KIND = "phantom_kind"
ROOT = "ROOT"
CAGE = "CAGE"
SCREEN = "SCREEN"
BAKE = "BAKE"
SOURCE_VISIBILITY = "phantom_source_hide_render"
MODEL_TYPES = {
    "MESH",
    "CURVE",
    "SURFACE",
    "META",
    "FONT",
    "CURVES",
    "POINTCLOUD",
    "VOLUME",
    "ARMATURE",
    "LATTICE",
    "EMPTY",
}


def _phantom_collection(scene):
    settings = scene.phantom_settings
    collection = settings.phantom_collection
    if collection is None:
        collection = bpy.data.collections.get("Phantoms")
    if collection is None:
        collection = bpy.data.collections.new("Phantoms")
    if collection.name not in scene.collection.children:
        scene.collection.children.link(collection)
    settings.phantom_collection = collection
    return collection


def _phantom_root(obj):
    if obj is None:
        return None
    if obj.get(KIND) == CAGE:
        return bpy.data.objects.get(obj.get("phantom_owner", ""))
    while obj is not None:
        if obj.get(KIND) == ROOT:
            return obj
        obj = obj.parent
    return None


def _members(root):
    result = {root}
    pending = list(root.children)
    while pending:
        child = pending.pop()
        result.add(child)
        pending.extend(child.children)
    return result


def _remember_source_visibility(root):
    for obj in _members(root):
        obj[SOURCE_VISIBILITY] = bool(obj.hide_render)


def _restore_source_visibility(root):
    for obj in _members(root):
        obj.hide_render = bool(obj.get(SOURCE_VISIBILITY, False))


def _cage(root):
    return bpy.data.objects.get(root.get("phantom_cage", ""))


def _configure_bounds(root, size):
    half = Vector(size) / 2.0
    old_center = Vector(root.get("phantom_box_center", root.location[:]))
    floor = root.get("phantom_floor_z")
    constraint = root.constraints.get("PhantomBounds")
    if constraint is None:
        constraint = root.constraints.new(type="LIMIT_LOCATION")
        constraint.name = "PhantomBounds"
    constraint.owner_space = "WORLD"
    for axis in "xyz":
        setattr(constraint, f"use_min_{axis}", False)
        setattr(constraint, f"use_max_{axis}", False)

    if floor is None:
        old_size = Vector(root.get("phantom_box_size", size))
        floor = old_center.z - old_size.z / 2.0
        old_world = root.matrix_world.copy()
        old_inverses = {child: child.matrix_parent_inverse.copy() for child in root.children}
        root.delta_location.z += floor - old_center.z
        new_world_inverse = root.matrix_world.inverted()
        for child, parent_inverse in old_inverses.items():
            child.matrix_parent_inverse = new_world_inverse @ old_world @ parent_inverse
        root["phantom_floor_z"] = floor

    old_position = Vector(
        root.get("phantom_box_applied_position", (old_center.x, old_center.y, floor))
    )
    if "phantom_box_applied_position" not in root:
        root.phantom_box_position = old_position
    desired_position = Vector(root.phantom_box_position)
    position_delta = desired_position - old_position
    if position_delta.length_squared > 0.0:
        root.delta_location += position_delta

    floor = desired_position.z
    center = Vector((desired_position.x, desired_position.y, floor + half.z))
    constraint.use_min_z = True
    constraint.min_z = floor
    root["phantom_floor_z"] = floor
    root["phantom_location_z_offset"] = root.delta_location.z
    root["phantom_box_applied_position"] = list(desired_position)
    root["phantom_box_center"] = list(center)
    root["phantom_box_size"] = list(size)

    cage = _cage(root)
    if cage is not None:
        cage.location = center
        cage.dimensions = size


def _new_mesh_object(name, vertices, faces, collection):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    return obj


def _set_active(context, obj):
    for selected in context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


def _top_ancestors(objects):
    selected = set(objects)
    result = set()
    for obj in selected:
        while obj.parent in selected:
            obj = obj.parent
        if obj.parent is not None:
            continue
        hierarchy = {obj, *obj.children_recursive}
        if (
            obj.get(KIND) not in {ROOT, CAGE, SCREEN, BAKE}
            and obj.type in MODEL_TYPES
            and not any(child.type == "GREASEPENCIL" for child in hierarchy)
        ):
            result.add(obj)
    return result


def _world_floor(objects, fallback):
    heights = []
    for root in objects:
        for obj in {root, *root.children_recursive}:
            if obj.type != "EMPTY":
                heights.extend((obj.matrix_world @ Vector(corner)).z for corner in obj.bound_box)
    return min(heights, default=fallback)


def _camera_floor_position(scene, anchor, fallback_floor):
    camera = scene.camera
    if camera is None:
        return Vector((anchor.x, anchor.y, fallback_floor))

    local_anchor = camera.matrix_world.inverted() @ anchor
    distance = -local_anchor.z
    if distance <= camera.data.clip_start:
        return Vector((anchor.x, anchor.y, fallback_floor))

    frame = camera.data.view_frame(scene=scene)
    bottom_y = min(
        corner.y
        if camera.data.type == "ORTHO"
        else corner.y * distance / abs(corner.z)
        for corner in frame
    )
    return camera.matrix_world @ Vector((local_anchor.x, bottom_y, local_anchor.z))


def _screen_transform(scene, root, distance):
    cage = _cage(root)
    if cage is None:
        raise ValueError("The selected Phantom has no cage")

    camera = scene.camera
    if camera is None:
        location = cage.matrix_world.translation + Vector(
            (0.0, cage.dimensions.y / 2.0 + distance, 0.0)
        )
        return location, Quaternion()

    camera_inverse = camera.matrix_world.inverted()
    cage_center = camera_inverse @ cage.matrix_world.translation
    cage_corners = [
        camera_inverse @ (cage.matrix_world @ Vector(corner)) for corner in cage.bound_box
    ]
    location = camera.matrix_world @ Vector(
        (cage_center.x, cage_center.y, min(corner.z for corner in cage_corners) - distance)
    )
    rotation = camera.matrix_world.to_quaternion() @ Quaternion(
        (1.0, 0.0, 0.0), -math.pi / 2.0
    )
    return location, rotation


def _position_screen(scene, screen, root):
    location, rotation = _screen_transform(
        scene, root, float(screen["phantom_screen_distance"])
    )
    screen.location = location
    screen.rotation_mode = "QUATERNION"
    screen.rotation_quaternion = rotation


def _reposition_screens(scene, root):
    owner_id = root.get("phantom_id")
    for screen in bpy.data.objects:
        if screen.get(KIND) == SCREEN and screen.get("phantom_cage_owner_id") == owner_id:
            _position_screen(scene, screen, root)


def _floor_offset(objects, root):
    floor = float(root["phantom_floor_z"])
    return floor - _world_floor(objects, floor)


def _model_offset(objects, root):
    points = [
        obj.matrix_world @ Vector(corner)
        for hierarchy_root in objects
        for obj in {hierarchy_root, *hierarchy_root.children_recursive}
        if obj.type != "EMPTY"
        for corner in obj.bound_box
    ]
    if not points:
        return Vector((0.0, 0.0, 0.0))
    minimum = Vector(
        (
            min(point.x for point in points),
            min(point.y for point in points),
            min(point.z for point in points),
        )
    )
    maximum = Vector(
        (
            max(point.x for point in points),
            max(point.y for point in points),
            max(point.z for point in points),
        )
    )
    cage_position = Vector(root.phantom_box_position)
    return Vector(
        (
            cage_position.x - (minimum.x + maximum.x) / 2.0,
            cage_position.y - (minimum.y + maximum.y) / 2.0,
            cage_position.z - minimum.z,
        )
    )


def _detach_import_extras(objects):
    excluded = {"CAMERA", "LIGHT", "LIGHT_PROBE", "SPEAKER", "GREASEPENCIL"}
    for obj in objects:
        if obj.type in excluded and obj.parent is not None:
            world_matrix = obj.matrix_world.copy()
            obj.parent = None
            obj.matrix_world = world_matrix


def _detach_storyboard_objects(root):
    grease_pencils = [obj for obj in _members(root) if obj.type == "GREASEPENCIL"]
    detached = 0
    for obj in grease_pencils:
        if obj.parent is None or obj.parent.type == "GREASEPENCIL":
            continue
        world_matrix = obj.matrix_world.copy()
        obj.parent = None
        obj.matrix_world = world_matrix
        detached += 1
    return detached


def _target_root(context):
    root = _phantom_root(context.active_object)
    if root is not None:
        return root
    roots = [obj for obj in context.scene.objects if obj.get(KIND) == ROOT]
    return roots[0] if len(roots) == 1 else None


def _parent_to_root(obj, root, world_offset=None):
    basis = obj.matrix_basis.copy()
    obj.parent = root
    obj.matrix_parent_inverse = root.matrix_world.inverted() @ Matrix.Translation(
        world_offset or Vector((0.0, 0.0, 0.0))
    )
    obj.matrix_basis = basis
    obj["phantom_character_root"] = True


def _ensure_bounds(root, fallback_size):
    _configure_bounds(root, Vector(root.get("phantom_box_size", fallback_size)))
    if root.type == "EMPTY":
        root.empty_display_type = "ARROWS"
        root.empty_display_size = 0.5
    return _detach_storyboard_objects(root)


def _clamp_to_box(root, location):
    location = Vector(location)
    floor = float(root.get("phantom_floor_z", root.location.z))
    raw_floor = floor - float(root.get("phantom_location_z_offset", 0.0))
    location.z = max(raw_floor, location.z)
    return location


def _screen_material(name, strength):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    emission.name = "PhantomEmission"
    emission.inputs["Color"].default_value = (0.18, 0.18, 0.18, 1.0)
    emission.inputs["Strength"].default_value = strength
    material.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return material


def _set_transparency(material):
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "DITHERED"
    elif hasattr(material, "blend_method"):
        material.blend_method = "BLEND"
    if hasattr(material, "use_transparency_overlap"):
        material.use_transparency_overlap = False


def _bake_material(image):
    material = bpy.data.materials.new(f"{image.name}_Material")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    emission = nodes.new("ShaderNodeEmission")
    texture = nodes.new("ShaderNodeTexImage")
    mixer = nodes.new("ShaderNodeMixShader")
    texture.image = image
    texture.interpolation = "Linear"
    texture.image_user.use_auto_refresh = True
    material.node_tree.links.new(texture.outputs["Color"], emission.inputs["Color"])
    material.node_tree.links.new(texture.outputs["Alpha"], mixer.inputs[0])
    material.node_tree.links.new(transparent.outputs[0], mixer.inputs[1])
    material.node_tree.links.new(emission.outputs[0], mixer.inputs[2])
    material.node_tree.links.new(mixer.outputs[0], output.inputs["Surface"])
    _set_transparency(material)
    return material, texture


def _projection_plane(scene, root, image, duration):
    camera = scene.camera
    local_center = camera.matrix_world.inverted() @ root.matrix_world.translation
    distance = -local_center.z
    if distance <= camera.data.clip_start:
        raise ValueError("Phantom must be in front of the active camera")

    vertices = []
    frame = camera.data.view_frame(scene=scene)
    for corner in frame:
        scale = 1.0 if camera.data.type == "ORTHO" else distance / abs(corner.z)
        vertices.append((corner.x * scale, corner.y * scale, -distance))
    min_x = min(vertex[0] for vertex in vertices)
    max_x = max(vertex[0] for vertex in vertices)
    min_y = min(vertex[1] for vertex in vertices)
    max_y = max(vertex[1] for vertex in vertices)
    uvs = [
        ((x - min_x) / (max_x - min_x), (y - min_y) / (max_y - min_y))
        for x, y, _z in vertices
    ]

    collection = _phantom_collection(scene)
    plane = _new_mesh_object(f"{root.name}_2D", vertices, [(0, 1, 2, 3)], collection)
    plane[KIND] = BAKE
    plane["phantom_owner_id"] = root["phantom_id"]
    plane.parent = camera
    uv_layer = plane.data.uv_layers.new(name="UVMap")
    for polygon in plane.data.polygons:
        for loop_index in polygon.loop_indices:
            vertex_index = plane.data.loops[loop_index].vertex_index
            uv_layer.data[loop_index].uv = uvs[vertex_index]

    material, texture = _bake_material(image)
    plane.data.materials.append(material)
    texture.image_user.frame_start = scene.frame_start
    texture.image_user.frame_duration = duration
    texture.image_user.frame_offset = 0
    return plane


def _remove_old_bakes(root, keep):
    owner_id = root.get("phantom_id")
    for obj in list(bpy.data.objects):
        if obj != keep and obj.get(KIND) == BAKE and obj.get("phantom_owner_id") == owner_id:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)


class PHANTOM_OT_create(bpy.types.Operator):
    bl_idname = "phantom.create"
    bl_label = "Create Phantom"
    bl_description = "Put the selected rig/model into a bounded Phantom container"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.phantom_settings
        collection = _phantom_collection(context.scene)
        selected = list(context.selected_objects)
        model_roots = _top_ancestors(selected)
        active = context.active_object
        anchor = active.matrix_world.translation.copy() if active else context.scene.cursor.location.copy()
        model_floor = _world_floor(model_roots, anchor.z)
        position = _camera_floor_position(context.scene, anchor, model_floor)
        floor = position.z
        center = Vector((position.x, position.y, floor + settings.box_size[2] / 2.0))

        root = bpy.data.objects.new("Phantom", None)
        collection.objects.link(root)
        root.empty_display_type = "ARROWS"
        root.empty_display_size = 0.5
        root.location = position
        root.show_in_front = True
        root[KIND] = ROOT
        root["is_phantom"] = True
        root["phantom_id"] = uuid.uuid4().hex
        root["phantom_floor_z"] = floor
        root.phantom_box_position = position
        root["phantom_box_applied_position"] = list(position)
        root["phantom_box_center"] = list(center)

        vertices, faces = unit_cube_geometry()
        cage = _new_mesh_object(f"{root.name}_Cage", vertices, faces, collection)
        cage[KIND] = CAGE
        cage["phantom_owner"] = root.name
        cage.display_type = "WIRE"
        cage.display.show_shadows = False
        cage.show_in_front = True
        cage.hide_render = True
        cage.hide_viewport = not settings.is_box_visible
        root["phantom_cage"] = cage.name
        _configure_bounds(root, settings.box_size)
        context.view_layer.update()

        model_offset = _model_offset(model_roots, root)
        for obj in model_roots:
            _parent_to_root(obj, root, model_offset)

        _set_active(context, root)
        self.report({"INFO"}, f"Created {root.name}")
        return {"FINISHED"}


class PHANTOM_OT_attach_selected(bpy.types.Operator):
    bl_idname = "phantom.attach_selected"
    bl_label = "Attach Selected to Phantom"
    bl_description = "Attach selected imported model or rig to the active or only Phantom"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        root = _target_root(context)
        if root is None:
            self.report({"ERROR"}, "Select the target Phantom last when the scene has several Phantoms")
            return {"CANCELLED"}

        objects = _top_ancestors(context.selected_objects)
        if not objects:
            self.report({"ERROR"}, "Select the top-level model or rig; storyboard strokes are not supported")
            return {"CANCELLED"}
        _ensure_bounds(root, context.scene.phantom_settings.box_size)
        context.view_layer.update()
        floor_offset = Vector((0.0, 0.0, _floor_offset(objects, root)))
        for obj in objects:
            _parent_to_root(obj, root, floor_offset)
        _set_active(context, root)
        self.report({"INFO"}, f"Attached {len(objects)} object hierarchy(s) to {root.name}")
        return {"FINISHED"}


class PHANTOM_OT_import_model(bpy.types.Operator, ImportHelper):
    bl_idname = "phantom.import_model"
    bl_label = "Load 3D Model"
    bl_description = "Import a 3D model from disk and attach it to the target Phantom"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ""
    filter_glob: StringProperty(
        default="*.fbx;*.obj;*.gltf;*.glb;*.stl;*.ply",
        options={"HIDDEN"},
    )
    target_name: StringProperty(options={"HIDDEN"})

    def invoke(self, context, _event):
        root = _target_root(context)
        if root is None:
            self.report({"ERROR"}, "Select the target Phantom first")
            return {"CANCELLED"}
        self.target_name = root.name
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        root = bpy.data.objects.get(self.target_name) or _target_root(context)
        if root is None or root.get(KIND) != ROOT:
            self.report({"ERROR"}, "Target Phantom no longer exists")
            return {"CANCELLED"}
        path = Path(self.filepath)
        if not path.is_file():
            self.report({"ERROR"}, "3D model file does not exist")
            return {"CANCELLED"}

        importers = {
            ".fbx": bpy.ops.wm.fbx_import,
            ".obj": bpy.ops.wm.obj_import,
            ".gltf": bpy.ops.import_scene.gltf,
            ".glb": bpy.ops.import_scene.gltf,
            ".stl": bpy.ops.wm.stl_import,
            ".ply": bpy.ops.wm.ply_import,
        }
        importer = importers.get(path.suffix.lower())
        if importer is None:
            self.report({"ERROR"}, f"Unsupported 3D format: {path.suffix}")
            return {"CANCELLED"}

        before = set(bpy.data.objects)
        try:
            result = importer(filepath=str(path))
        except Exception as exc:
            for obj in set(bpy.data.objects) - before:
                bpy.data.objects.remove(obj, do_unlink=True)
            self.report({"ERROR"}, f"Blender could not import this model: {exc}")
            return {"CANCELLED"}
        if "FINISHED" not in result:
            for obj in set(bpy.data.objects) - before:
                bpy.data.objects.remove(obj, do_unlink=True)
            self.report({"ERROR"}, "Blender cancelled the model import")
            return {"CANCELLED"}

        imported = set(bpy.data.objects) - before
        _detach_import_extras(imported)
        objects = _top_ancestors(imported)
        if not objects:
            for obj in imported:
                bpy.data.objects.remove(obj, do_unlink=True)
            self.report({"ERROR"}, "The file did not contain a supported 3D model")
            return {"CANCELLED"}

        _ensure_bounds(root, context.scene.phantom_settings.box_size)
        collection = _phantom_collection(context.scene)
        for obj in imported:
            if obj.type not in MODEL_TYPES:
                continue
            if obj.name not in collection.objects:
                collection.objects.link(obj)
            for source_collection in list(obj.users_collection):
                if source_collection != collection:
                    source_collection.objects.unlink(obj)
        context.view_layer.update()
        model_offset = _model_offset(objects, root)
        for obj in objects:
            _parent_to_root(obj, root, model_offset)
        _set_active(context, root)
        self.report({"INFO"}, f"Loaded {path.name} into {root.name}")
        return {"FINISHED"}


class PHANTOM_OT_update_box(bpy.types.Operator):
    bl_idname = "phantom.update_box"
    bl_label = "Apply Cage Size / Position"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _phantom_root(context.active_object) is not None

    def execute(self, context):
        root = _phantom_root(context.active_object)
        _configure_bounds(root, context.scene.phantom_settings.box_size)
        if root.type == "EMPTY":
            root.empty_display_type = "ARROWS"
            root.empty_display_size = 0.5
        _detach_storyboard_objects(root)
        context.view_layer.update()
        _reposition_screens(context.scene, root)
        return {"FINISHED"}


class PHANTOM_OT_toggle_box(bpy.types.Operator):
    bl_idname = "phantom.toggle_box"
    bl_label = "Toggle Cage"

    def execute(self, context):
        settings = context.scene.phantom_settings
        settings.is_box_visible = not settings.is_box_visible
        return {"FINISHED"}


class PHANTOM_OT_control(bpy.types.Operator):
    bl_idname = "phantom.control"
    bl_label = "Control Phantom"
    bl_description = "WASD: X/Y, Q/E: Z, arrows: rotate, K: keyframe, Esc: stop"

    _root_name = ""

    @classmethod
    def poll(cls, context):
        return _phantom_root(context.active_object) is not None

    def invoke(self, context, _event):
        root = _phantom_root(context.active_object)
        detached = _ensure_bounds(root, context.scene.phantom_settings.box_size)
        if detached:
            self.report({"WARNING"}, f"Detached {detached} storyboard stroke object(s) from the Phantom")
        self._root_name = root.name
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Phantom: WASD move, Q/E height, arrows rotate, K keyframe, Esc stop"
        )
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        root = bpy.data.objects.get(self._root_name)
        if root is None or event.type in {"ESC", "RIGHTMOUSE"}:
            context.workspace.status_text_set(None)
            return {"FINISHED"}
        if event.value != "PRESS":
            return {"PASS_THROUGH"}

        settings = context.scene.phantom_settings
        step = settings.move_step
        moves = {
            "W": Vector((0.0, step, 0.0)),
            "S": Vector((0.0, -step, 0.0)),
            "A": Vector((-step, 0.0, 0.0)),
            "D": Vector((step, 0.0, 0.0)),
            "Q": Vector((0.0, 0.0, -step)),
            "E": Vector((0.0, 0.0, step)),
        }
        changed = False
        if event.type in moves:
            root.location = _clamp_to_box(root, root.location + moves[event.type])
            changed = True
        elif event.type in {"LEFT_ARROW", "RIGHT_ARROW"}:
            direction = 1.0 if event.type == "LEFT_ARROW" else -1.0
            root.rotation_euler.z += direction * math.radians(5.0)
            changed = True
        elif event.type == "K":
            root.keyframe_insert(data_path="location")
            root.keyframe_insert(data_path="rotation_euler")
            self.report({"INFO"}, f"Keyframe inserted at {context.scene.frame_current}")
            return {"RUNNING_MODAL"}
        else:
            return {"PASS_THROUGH"}

        if changed and settings.auto_key:
            root.keyframe_insert(data_path="location")
            root.keyframe_insert(data_path="rotation_euler")
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        context.workspace.status_text_set(None)


class PHANTOM_OT_set_depth(bpy.types.Operator):
    bl_idname = "phantom.set_depth"
    bl_label = "Set 2D Depth"
    bl_options = {"REGISTER", "UNDO"}

    role: EnumProperty(
        items=(
            ("BACKGROUND", "Background", "Lock Z to 10"),
            ("PHANTOM", "Phantom", "Lock Z to 0"),
            ("FOREGROUND", "Foreground", "Lock Z to -10"),
        )
    )

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        depth = {"BACKGROUND": 10.0, "PHANTOM": 0.0, "FOREGROUND": -10.0}[self.role]
        obj = context.active_object
        obj.location.z = depth
        constraint = obj.constraints.get("PhantomDepth")
        if constraint is None:
            constraint = obj.constraints.new(type="LIMIT_LOCATION")
            constraint.name = "PhantomDepth"
        constraint.owner_space = "WORLD"
        constraint.use_min_z = True
        constraint.use_max_z = True
        constraint.min_z = depth
        constraint.max_z = depth
        obj["phantom_depth_role"] = self.role
        return {"FINISHED"}


class PHANTOM_OT_create_screen(bpy.types.Operator):
    bl_idname = "phantom.create_screen"
    bl_label = "Create Curved Screen"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.phantom_settings
        target = _target_root(context)
        if target is None or _cage(target) is None:
            self.report({"ERROR"}, "Select a Phantom with a cage first")
            return {"CANCELLED"}
        vertices, faces, uvs = curved_screen_geometry(
            settings.screen_width,
            settings.screen_height,
            settings.screen_curvature,
            settings.screen_segments,
        )
        collection = _phantom_collection(context.scene)
        screen = _new_mesh_object("Phantom_Backdrop", vertices, faces, collection)
        screen[KIND] = SCREEN
        screen["phantom_cage_owner_id"] = target["phantom_id"]
        screen["phantom_screen_distance"] = settings.screen_distance
        context.view_layer.update()
        _position_screen(context.scene, screen, target)
        uv_layer = screen.data.uv_layers.new(name="UVMap")
        for polygon in screen.data.polygons:
            for loop_index in polygon.loop_indices:
                vertex_index = screen.data.loops[loop_index].vertex_index
                uv_layer.data[loop_index].uv = uvs[vertex_index]
        screen.data.materials.append(_screen_material("Phantom_Backdrop_Material", settings.screen_emission))

        light_data = bpy.data.lights.new(f"{screen.name}_Light", type="AREA")
        light_data.energy = settings.screen_light_energy
        light_data.color = settings.screen_light_color
        light_data.shape = "RECTANGLE"
        light_data.size = settings.screen_width
        light_data.size_y = settings.screen_height
        light = bpy.data.objects.new(light_data.name, light_data)
        collection.objects.link(light)
        light.parent = screen
        light.location = (0.0, -0.15, 0.0)
        light.rotation_euler.x = -math.pi / 2.0

        _set_active(context, screen)
        return {"FINISHED"}


class PHANTOM_OT_load_media(bpy.types.Operator, ImportHelper):
    bl_idname = "phantom.load_media"
    bl_label = "Load Image / Video"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ""
    filter_glob: StringProperty(
        default="*.png;*.jpg;*.jpeg;*.webp;*.tif;*.tiff;*.exr;*.mp4;*.mov;*.avi;*.mkv;*.gif",
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.get(KIND) == SCREEN

    def execute(self, context):
        path = Path(self.filepath)
        if not path.is_file():
            self.report({"ERROR"}, "Media file does not exist")
            return {"CANCELLED"}
        try:
            image = bpy.data.images.load(str(path), check_existing=True)
            if path.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".gif"}:
                image.source = "MOVIE"
        except Exception as exc:
            self.report({"ERROR"}, f"Blender could not load this media: {exc}")
            return {"CANCELLED"}

        material = context.active_object.data.materials[0]
        nodes = material.node_tree.nodes
        texture = nodes.get("PhantomMedia") or nodes.new("ShaderNodeTexImage")
        texture.name = "PhantomMedia"
        texture.image = image
        texture.image_user.frame_start = context.scene.frame_start
        texture.image_user.frame_duration = max(1, image.frame_duration)
        texture.image_user.use_auto_refresh = True
        texture.image_user.use_cyclic = True
        emission = nodes.get("PhantomEmission")
        for link in list(emission.inputs["Color"].links):
            material.node_tree.links.remove(link)
        material.node_tree.links.new(texture.outputs["Color"], emission.inputs["Color"])
        context.active_object["phantom_media"] = str(path)
        return {"FINISHED"}


class PHANTOM_OT_bake(bpy.types.Operator):
    bl_idname = "phantom.bake"
    bl_label = "Bake to 2D Projection"
    bl_description = "Render the selected Phantom to a held transparent PNG sequence"

    @classmethod
    def poll(cls, context):
        return _phantom_root(context.active_object) is not None

    def execute(self, context):
        scene = context.scene
        root = _phantom_root(context.active_object)
        if scene.camera is None:
            self.report({"ERROR"}, "Set an active scene camera first")
            return {"CANCELLED"}
        if not bpy.data.filepath:
            self.report({"ERROR"}, "Save the .blend file before baking")
            return {"CANCELLED"}

        settings = scene.phantom_settings
        step = int(settings.bake_step)
        duration = scene.frame_end - scene.frame_start + 1
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", root.name).strip("_") or "Phantom"
        output_dir = Path(bpy.path.abspath(f"//phantom_bakes/{safe_name}_{root['phantom_id'][:8]}"))
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        render_dir = output_dir.with_name(f".{output_dir.name}_{uuid.uuid4().hex[:8]}.tmp")
        render_dir.mkdir()
        members = _members(root)
        if not root.get("phantom_using_bake", False):
            _remember_source_visibility(root)
        objects = list(scene.objects)
        hidden_state = {obj: obj.hide_render for obj in objects}
        render = scene.render
        old_render = (
            render.filepath,
            render.image_settings.file_format,
            render.image_settings.color_mode,
            render.film_transparent,
            getattr(render, "use_freestyle", None),
            getattr(render, "line_thickness", None),
            scene.frame_current,
        )
        sample_count = math.ceil(duration / step)
        context.window_manager.progress_begin(0, sample_count)
        error = None

        try:
            for obj in objects:
                if obj in members:
                    obj.hide_render = bool(obj.get(SOURCE_VISIBILITY, hidden_state[obj]))
                elif obj.type not in {"CAMERA", "LIGHT"}:
                    obj.hide_render = True
            render.image_settings.file_format = "PNG"
            render.image_settings.color_mode = "RGBA"
            render.film_transparent = True
            if old_render[4] is not None:
                render.use_freestyle = True
            if old_render[5] is not None:
                render.line_thickness = settings.outline_thickness

            for sample_index, (frame, held_frames) in enumerate(
                held_frame_groups(scene.frame_start, scene.frame_end, step)
            ):
                scene.frame_set(frame)
                sample_path = render_dir / "_sample.png"
                render.filepath = str(sample_path)
                bpy.ops.render.render(write_still=True)
                for held_frame in held_frames:
                    sequence_index = held_frame - scene.frame_start + 1
                    shutil.copyfile(sample_path, render_dir / f"phantom_{sequence_index:06d}.png")
                sample_path.unlink(missing_ok=True)
                context.window_manager.progress_update(sample_index + 1)
        except Exception as exc:
            error = str(exc)
        finally:
            for obj, hidden in hidden_state.items():
                if obj.name in bpy.data.objects:
                    obj.hide_render = hidden
            render.filepath = old_render[0]
            render.image_settings.file_format = old_render[1]
            render.image_settings.color_mode = old_render[2]
            render.film_transparent = old_render[3]
            if old_render[4] is not None:
                render.use_freestyle = old_render[4]
            if old_render[5] is not None:
                render.line_thickness = old_render[5]
            scene.frame_set(old_render[6])
            context.window_manager.progress_end()

        if error:
            shutil.rmtree(render_dir, ignore_errors=True)
            self.report({"ERROR"}, f"Bake failed: {error}")
            return {"CANCELLED"}

        backup_dir = output_dir.with_name(f".{output_dir.name}.backup")
        old_bakes = {
            obj
            for obj in bpy.data.objects
            if obj.get(KIND) == BAKE
            and obj.get("phantom_owner_id") == root.get("phantom_id")
        }
        old_moved = False
        swapped = False
        try:
            shutil.rmtree(backup_dir, ignore_errors=True)
            if output_dir.exists():
                output_dir.rename(backup_dir)
                old_moved = True
            render_dir.rename(output_dir)
            swapped = True
            first_image = output_dir / "phantom_000001.png"
            image = bpy.data.images.load(str(first_image), check_existing=False)
            image.source = "SEQUENCE"
            plane = _projection_plane(scene, root, image, duration)
            _remove_old_bakes(root, plane)
            shutil.rmtree(backup_dir, ignore_errors=True)
        except Exception as exc:
            for obj in list(bpy.data.objects):
                if (
                    obj not in old_bakes
                    and obj.get(KIND) == BAKE
                    and obj.get("phantom_owner_id") == root.get("phantom_id")
                ):
                    mesh = obj.data
                    bpy.data.objects.remove(obj, do_unlink=True)
                    if mesh.users == 0:
                        bpy.data.meshes.remove(mesh)
            if swapped:
                shutil.rmtree(output_dir, ignore_errors=True)
            if old_moved and backup_dir.exists():
                backup_dir.rename(output_dir)
            shutil.rmtree(render_dir, ignore_errors=True)
            self.report({"ERROR"}, f"Projection creation failed; previous bake restored: {exc}")
            return {"CANCELLED"}

        for obj in members:
            obj.hide_render = True
        root["phantom_using_bake"] = True
        plane.hide_render = False
        _set_active(context, root)
        self.report({"INFO"}, f"Baked {sample_count} poses to {output_dir}")
        return {"FINISHED"}


class PHANTOM_OT_use_bake(bpy.types.Operator):
    bl_idname = "phantom.use_bake"
    bl_label = "Switch Phantom Render"

    use_bake: BoolProperty(default=True)

    @classmethod
    def poll(cls, context):
        return _phantom_root(context.active_object) is not None

    def execute(self, context):
        root = _phantom_root(context.active_object)
        if self.use_bake:
            if not root.get("phantom_using_bake", False):
                _remember_source_visibility(root)
            for obj in _members(root):
                obj.hide_render = True
        else:
            _restore_source_visibility(root)
        root["phantom_using_bake"] = self.use_bake

        owner_id = root.get("phantom_id")
        for obj in bpy.data.objects:
            if obj.get(KIND) == BAKE and obj.get("phantom_owner_id") == owner_id:
                obj.hide_render = not self.use_bake
        return {"FINISHED"}


classes = (
    PHANTOM_OT_create,
    PHANTOM_OT_attach_selected,
    PHANTOM_OT_import_model,
    PHANTOM_OT_update_box,
    PHANTOM_OT_toggle_box,
    PHANTOM_OT_control,
    PHANTOM_OT_set_depth,
    PHANTOM_OT_create_screen,
    PHANTOM_OT_load_media,
    PHANTOM_OT_bake,
    PHANTOM_OT_use_bake,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
