import bpy

from .operators import KIND, SCREEN, _phantom_root


class PHANTOM_PT_storyboard(bpy.types.Panel):
    bl_label = "Phantom Storyboard"
    bl_idname = "PHANTOM_PT_storyboard"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Phantom"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.phantom_settings
        root = _phantom_root(context.active_object)

        box = layout.box()
        box.label(text="Phantom", icon="ARMATURE_DATA")
        box.label(text="Select a rig/model before Create", icon="INFO")
        box.operator("phantom.create", icon="ADD")
        box.operator("phantom.import_model", icon="IMPORT")
        box.operator("phantom.attach_selected", icon="OUTLINER_OB_ARMATURE")
        box.prop(settings, "is_box_visible")
        size_section = box.box()
        size_section.prop(
            settings,
            "show_cage_size",
            text="Cage Size",
            icon="TRIA_DOWN" if settings.show_cage_size else "TRIA_RIGHT",
            emboss=False,
        )
        if settings.show_cage_size:
            size_section.prop(settings, "box_size", text="")

        position_section = box.box()
        position_section.prop(
            settings,
            "show_cage_position",
            text="Cage Floor Position",
            icon="TRIA_DOWN" if settings.show_cage_position else "TRIA_RIGHT",
            emboss=False,
        )
        if settings.show_cage_position:
            if root is not None:
                position_section.prop(root, "phantom_box_position", text="")
            else:
                position_section.label(text="Select a Phantom")
        row = box.row(align=True)
        row.operator("phantom.update_box", text="Apply Size / Position", icon="FILE_REFRESH")
        row.operator("phantom.toggle_box", text="Toggle", icon="HIDE_OFF")

        controls = layout.box()
        controls.label(text=root.name if root else "No Phantom selected", icon="EMPTY_AXIS")
        controls.label(text="Arrow Empty = controller; do not delete", icon="INFO")
        controls.prop(settings, "move_step")
        controls.prop(settings, "auto_key")
        controls.operator("phantom.control", icon="PLAY")
        controls.label(text="WASD local / Q E vertical / arrows rotate")

        bake = layout.box()
        bake.label(text="2D Projection", icon="RENDER_ANIMATION")
        bake.prop(settings, "bake_step")
        bake.prop(settings, "outline_thickness")
        bake.operator("phantom.bake", icon="RENDER_STILL")
        row = bake.row(align=True)
        use_2d = row.operator("phantom.use_bake", text="Use 2D")
        use_2d.use_bake = True
        use_3d = row.operator("phantom.use_bake", text="Use 3D")
        use_3d.use_bake = False

        screen = layout.box()
        screen.label(text="Curved Backdrop", icon="IMAGE_BACKGROUND")
        row = screen.row(align=True)
        row.prop(settings, "screen_width")
        row.prop(settings, "screen_height")
        screen.prop(settings, "screen_curvature")
        screen.prop(settings, "screen_distance")
        screen.prop(settings, "screen_segments")
        screen.prop(settings, "screen_emission")
        screen.prop(settings, "screen_light_energy")
        screen.prop(settings, "screen_light_color")
        screen.operator("phantom.create_screen", icon="MESH_GRID")
        media = screen.row()
        media.enabled = context.active_object is not None and context.active_object.get(KIND) == SCREEN
        media.operator("phantom.load_media", icon="FILEBROWSER")

        depth = layout.box()
        depth.label(text="Z Order")
        row = depth.row(align=True)
        background = row.operator("phantom.set_depth", text="Back")
        background.role = "BACKGROUND"
        phantom = row.operator("phantom.set_depth", text="Phantom")
        phantom.role = "PHANTOM"
        foreground = row.operator("phantom.set_depth", text="Front")
        foreground.role = "FOREGROUND"


classes = (PHANTOM_PT_storyboard,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
