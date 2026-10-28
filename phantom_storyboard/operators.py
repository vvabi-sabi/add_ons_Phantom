import math
import re
import shutil
import uuid
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ImportHelper
from mathutils import Quaternion, Vector

from .geometry import curved_screen_geometry, held_frame_groups, unit_cube_geometry


KIND = "phantom_kind"
ROOT = "ROOT"
CAGE = "CAGE"
SCREEN = "SCREEN"
BAKE = "BAKE"
SOURCE_VISIBILITY = "phantom_source_hide_render"


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
    center = Vector(root.get("phantom_box_center", root.location[:]))
    half = Vector(size) / 2.0
    constraint = root.constraints.get("PhantomBounds")
    if constraint is None:
        constraint = root.constraints.new(type="LIMIT_LOCATION")
        constraint.name = "PhantomBounds"
    constraint.owner_space = "WORLD"
    for axis, low, high in zip("xyz", center - half, center + half):
        setattr(constraint, f"use_min_{axis}", True)
        setattr(constraint, f"use_max_{axis}", True)
        setattr(constraint, f"min_{axis}", low)
        setattr(constraint, f"max_{axis}", high)
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
    result = set()
    for obj in objects:
        while obj.parent is not None:
            obj = obj.parent
        if obj.get(KIND) not in {ROOT, CAGE, SCREEN, BAKE}:
            result.add(obj)
    return result


def _target_root(context):
    root = _phantom_root(context.active_object)
    if root is not None:
        return root
    roots = [obj for obj in context.scene.objects if obj.get(KIND) == ROOT]
    return roots[0] if len(roots) == 1 else None


def _parent_to_root(obj, root):
    basis = obj.matrix_basis.copy()
    obj.parent = root
    obj.matrix_parent_inverse = root.matrix_world.inverted()
    obj.matrix_basis = basis


def _clamp_to_box(root, location):
    center = Vector(root.get("phantom_box_center", root.location[:]))
    half = Vector(root.get("phantom_box_size", (8.0, 4.0, 5.0))) / 2.0
    return Vector(
        max(center[index] - half[index], min(center[index] + half[index], location[index]))
        for index in range(3)
    )


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
        active = context.active_object
        center = active.matrix_world.translation.copy() if active else context.scene.cursor.location.copy()

        root = bpy.data.objects.new("Phantom", None)
        collection.objects.link(root)
        root.empty_display_type = "CUBE"
        root.empty_display_size = 0.35
        root.location = center
        root.show_in_front = True
        root[KIND] = ROOT
        root["is_phantom"] = True
        root["phantom_id"] = uuid.uuid4().hex
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

        for obj in _top_ancestors(selected):
            _parent_to_root(obj, root)

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
            self.report({"ERROR"}, "Select an imported model or rig")
            return {"CANCELLED"}
        for obj in objects:
            _parent_to_root(obj, root)
        _set_active(context, root)
        self.report({"INFO"}, f"Attached {len(objects)} object hierarchy(s) to {root.name}")
        return {"FINISHED"}


class PHANTOM_OT_update_box(bpy.types.Operator):
    bl_idname = "phantom.update_box"
    bl_label = "Apply Cage Size"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _phantom_root(context.active_object) is not None

    def execute(self, context):
        root = _phantom_root(context.active_object)
        _configure_bounds(root, context.scene.phantom_settings.box_size)
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
        vertices, faces, uvs = curved_screen_geometry(
            settings.screen_width,
            settings.screen_height,
            settings.screen_curvature,
            settings.screen_segments,
        )
        collection = _phantom_collection(context.scene)
        screen = _new_mesh_object("Phantom_Backdrop", vertices, faces, collection)
        screen[KIND] = SCREEN
        target = _target_root(context)
        target_location = (
            target.matrix_world.translation.copy()
            if target is not None
            else context.scene.cursor.location.copy()
        )
        camera = context.scene.camera
        if camera is not None:
            direction = target_location - camera.matrix_world.translation
            if direction.length == 0.0:
                direction = camera.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))
            direction.normalize()
            screen.location = target_location + direction * settings.screen_distance
            screen.rotation_mode = "QUATERNION"
            screen.rotation_quaternion = camera.matrix_world.to_quaternion() @ Quaternion(
                (1.0, 0.0, 0.0), -math.pi / 2.0
            )
        else:
            screen.location = target_location + Vector((0.0, settings.screen_distance, 0.0))
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
