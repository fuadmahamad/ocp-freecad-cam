import math
import typing
from abc import ABC
from itertools import pairwise
from typing import Optional

from cadquery.units import DEG2RAD
from OCP.AIS import (
    AIS_Circle,
    AIS_Line,
    AIS_MultipleConnectedInteractive,
    AIS_Shape,
    AIS_WireFrame,
)
from OCP.Aspect import Aspect_TOL_SOLID
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
from OCP.BRepLib import BRepLib
from OCP.GC import GC_MakeArcOfCircle
from OCP.GCE2d import GCE2d_MakeSegment
from OCP.Geom import (
    Geom_CartesianPoint,
    Geom_Circle,
    Geom_ConicalSurface,
    Geom_CylindricalSurface,
    Geom_Surface,
)
from OCP.Geom2d import Geom2d_Line
from OCP.GeomAPI import GeomAPI_ProjectPointOnCurve
from OCP.gp import gp_Ax2, gp_Ax3, gp_Dir, gp_Dir2d, gp_Pnt, gp_Pnt2d, gp_Trsf, gp_Vec
from OCP.Graphic3d import Graphic3d_AspectFillArea3d
from OCP.Prs3d import Prs3d_LineAspect, Prs3d_ShadingAspect
from OCP.Quantity import Quantity_Color, Quantity_NOC_GREEN, Quantity_NOC_YELLOW
from OCP.TopoDS import TopoDS_Edge
from Path.Post.Command import buildPostList

if typing.TYPE_CHECKING:
    from Path.Main import Job as FC_Job


class VisualCommand(ABC):
    def __init__(self, *, x, y, z, **kwargs):
        self.x = x
        self.y = y
        self.z = z

    def to_ais(self, start: "VisualCommand"):
        raise NotImplemented

    def __eq__(self, other):
        if isinstance(other, VisualCommand):
            return self.x == other.x and self.y == other.y and self.z == other.z
        raise TypeError(f"Can not compare {type(self)} with {type(other)}")


class LinearVisualCommand(VisualCommand):
    def to_ais(self, start: "VisualCommand"):
        if start == self:
            return None
        start_point = Geom_CartesianPoint(start.x, start.y, start.z)
        end_point = Geom_CartesianPoint(self.x, self.y, self.z)
        # todo empty lines
        return AIS_Line(start_point, end_point)


class RapidVisualCommand(LinearVisualCommand):
    pass


class ArcVisualCommand(LinearVisualCommand, ABC):
    def __init__(self, *, arc_plane, i=None, j=None, k=None, **kwargs):
        # TODO: XY has i and j, not k!
        super().__init__(**kwargs)
        self.arc_plane = arc_plane
        self.i = i
        self.j = j
        self.k = k

    def circle_normal_dir(self, circle_normal: gp_Vec):
        raise NotImplemented

    @property
    def clockwise(self):
        raise NotImplemented

    def to_ais(self, start: VisualCommand):
        if self.arc_plane == (0, 0, 1):
            if self.i is None or self.j is None:
                raise ValueError("I and J must be defined for XY arc")
            cx = start.x + self.i
            cy = start.y + self.j
            cz = (start.z + self.z) / 2.0
            i = self.i
            j = self.j
            k = cz - start.z
            height = k * 2
            top_center = gp_Pnt(cx, cy, start.z)
            bottom_center = gp_Pnt(cx, cy, self.z)
            radius = math.sqrt(self.i**2 + self.j**2)
            full_circle = start.x == self.x and start.y == self.y

        elif self.arc_plane == (0, 1, 0):
            if self.i is None or self.k is None:
                raise ValueError("I and K must be defined for XZ arc")
            cx = start.x + self.i
            cy = (start.y + self.y) / 2.0
            cz = start.z + self.k
            i = self.i
            j = cy - start.y
            k = self.k
            height = j * 2
            top_center = gp_Pnt(cx, start.y, cz)
            bottom_center = gp_Pnt(cx, self.y, cz)
            radius = math.sqrt(self.i**2 + self.k**2)
            full_circle = start.x == self.x and start.z == self.z

        elif self.arc_plane == (1, 0, 0):
            if self.j is None or self.k is None:
                raise ValueError("J and K must be defined for YZ arc")
            cx = (start.x + self.x) / 2.0
            cy = start.y + self.j
            cz = start.z + self.k
            i = cx - start.x
            j = self.j
            k = self.k
            height = i * 2
            top_center = gp_Pnt(start.x, cy, cz)
            bottom_center = gp_Pnt(self.x, cy, cz)
            radius = math.sqrt(self.j**2 + self.k**2)
            full_circle = start.y == self.y and start.z == self.z

        else:
            raise ValueError(f"Unknown arc plane: {self.arc_plane}")

        # Exberiment with helical approach
        start_point = gp_Pnt(start.x, start.y, start.z)

        c = gp_Pnt(cx, cy, cz)
        if height:
            # FreeCAD helix always starts from (X+radius, Y)
            # Same thing for CQ helix. Only need to calc pitch
            # FreeCAD also produces only full or half circle, so
            # exploit that :-D
            if full_circle:
                pitch = abs(height)
            else:
                pitch = abs(height / 2)
            e = makeHelix(
                pitch,
                height,
                radius,
                top_center,
                gp_Dir(*self.arc_plane),
                lefthand=not self.clockwise,
            )
            shape = AIS_Shape(e)
            shape.SetColor(Quantity_Color(Quantity_NOC_GREEN))
            return shape

        # XY
        arc_plane = gp_Vec(*self.arc_plane)
        cv = gp_Vec(i, j, k)
        forward = cv.Crossed(arc_plane)
        circle_normal = cv.Crossed(forward)
        circle_normal_dir = self.circle_normal_dir(circle_normal)

        forward_dir = gp_Dir(forward.X(), forward.Y(), forward.Z())
        circle_ax = gp_Ax2(c, circle_normal_dir, forward_dir)
        geom_circle = Geom_Circle(circle_ax, radius)
        start_point = gp_Pnt(start.x, start.y, start.z)
        end_point = gp_Pnt(self.x, self.y, self.z)

        curve = GC_MakeArcOfCircle(
            geom_circle.Circ(), start_point, end_point, True
        ).Value()
        shape = AIS_Shape(BRepBuilderAPI_MakeEdge(curve).Edge())
        shape.SetColor(Quantity_Color(Quantity_NOC_YELLOW))
        return shape


def makeHelix(
    pitch: float,
    height: float,
    radius: float,
    center: gp_Pnt,
    dir: gp_Dir,
    angle: float = 360.0,
    lefthand: bool = False,
) -> "TopoDS_Edge":
    """
    Make a helix with a given pitch, height and radius
    By default a cylindrical surface is used to create the helix. If
    the fourth parameter is set (the apex given in degree) a conical surface
    is used instead.

    Implementation copied from CadQuery, which looks like it borrowed the code from
    FreeCAD.. unable to trace the original author from commit history, but
    thanks CQ and FreeCAD contributors!
    """

    # 1. build underlying cylindrical/conical surface
    if angle == 360.0:
        geom_surf: Geom_Surface = Geom_CylindricalSurface(gp_Ax3(center, dir), radius)
    else:
        geom_surf = Geom_ConicalSurface(
            gp_Ax3(center, dir),
            angle * DEG2RAD,
            radius,
        )

    # 2. construct an segment in the u,v domain
    if lefthand:
        geom_line = Geom2d_Line(gp_Pnt2d(0.0, 0.0), gp_Dir2d(-2 * math.pi, pitch))
    else:
        geom_line = Geom2d_Line(gp_Pnt2d(0.0, 0.0), gp_Dir2d(2 * math.pi, pitch))

    # 3. put it together into am edge
    n_turns = height / pitch
    u_start = geom_line.Value(0.0)
    u_stop = geom_line.Value(n_turns * math.sqrt((2 * math.pi) ** 2 + pitch**2))
    geom_seg = GCE2d_MakeSegment(u_start, u_stop).Value()

    e = BRepBuilderAPI_MakeEdge(geom_seg, geom_surf).Edge()
    return e


class CWArcVisualCommand(ArcVisualCommand):
    def circle_normal_dir(self, circle_normal: gp_Vec):
        return gp_Dir(circle_normal.X(), circle_normal.Y(), circle_normal.Z())

    @property
    def clockwise(self):
        return True


class CCWArcVisualCommand(ArcVisualCommand):
    def circle_normal_dir(self, circle_normal: gp_Vec):
        return gp_Dir(-circle_normal.X(), -circle_normal.Y(), -circle_normal.Z())

    @property
    def clockwise(self):
        return False


def visualize_fc_job(job, inverse_trsf: gp_Trsf):
    """
    Visualize a FreeCAD job
    https://wiki.freecad.org/Path_scripting#The_FreeCAD_Internal_GCode_Format
    """
    params = {"arc_plane": (0, 0, 1)}
    relative = False
    canned = False
    canned_r = False
    canned_z = None

    visual_commands = []

    postlist = buildPostList(job)
    print("All ops", [o.Name for o in job.Proxy.allOperations()])
    for name, sub_op_list in postlist:
        for op in sub_op_list:
            if hasattr(op, "Path"):
                commands = op.Path.Commands
            else:
                commands = op.Proxy.commandlist

            for command in commands:
                new_params = {k.lower(): v for k, v in command.Parameters.items()}
                if relative:
                    # Convert to absolute
                    rel_attrs = ["x", "y", "z"]
                    for attr in rel_attrs:
                        if attr in new_params:
                            # This will catch fire if params does not have a previous value
                            # Not sure if FreeCAD generates code like that, so lets see
                            # if it needs to be handled..
                            new_params[attr] = new_params[attr] + params[attr]
                combined_params = {**params, **new_params}
                print("Match", command.Name)
                match command.Name:
                    case "G0":
                        params = add_command(
                            visual_commands, RapidVisualCommand, **combined_params
                        )
                    case "G1":
                        params = add_command(
                            visual_commands, LinearVisualCommand, **combined_params
                        )
                    case "G2":
                        params = add_command(
                            visual_commands, CWArcVisualCommand, **combined_params
                        )
                    case "G3":
                        params = add_command(
                            visual_commands, CCWArcVisualCommand, **combined_params
                        )
                    case "G17":
                        params["arc_plane"] = (0, 0, 1)
                    case "G18":
                        params["arc_plane"] = (0, 1, 0)
                    case "G19":
                        params["arc_plane"] = (1, 0, 0)
                    case "G81":
                        # Canned cycle
                        # FreeCAD canned cycle looks to be in a format like
                        # G81 X2.000 Y-2.000 Z-2.000 R3.000
                        # So we issue two commands, first going down to Z
                        # and then coming back up to R
                        if not canned:
                            if not canned_r:
                                canned_z = params["z"]
                            canned = True

                        add_command(
                            visual_commands, LinearVisualCommand, **combined_params
                        )
                        if canned_r:
                            combined_params = {
                                **combined_params,
                                "z": combined_params["r"],
                            }
                        else:
                            combined_params = {**combined_params, "z": canned_z}
                        params = add_command(
                            visual_commands, LinearVisualCommand, **combined_params
                        )

                    case "G80":
                        # End of canned cycle
                        canned = False

                    case "G91":
                        relative = True
                        print("Relative mode on")
                    case "G90":
                        relative = False
                        print("Relative mode off")

                    case "G98":
                        # Canned cycle mode, probably not relevant
                        canned_r = False
                    case "G99":
                        canned_r = True

                    case _:
                        if command.Name.startswith("("):
                            continue
                        print("Unknown gcode", command.Name)

    return visual_commands_to_ais(visual_commands, inverse_trsf=inverse_trsf)


def visual_commands_to_ais(
    visual_commands: list[VisualCommand], inverse_trsf: Optional[gp_Trsf] = None
):
    print("to ais", visual_commands)
    if len(visual_commands) < 2:
        return

    group = AIS_MultipleConnectedInteractive()
    if inverse_trsf:
        group.SetLocalTransformation(inverse_trsf)

    for start, end in pairwise(visual_commands):
        shape = end.to_ais(start)
        if shape:
            group.Connect(shape)

    # UnsetSelectionMode?
    # Color?
    return group


def add_command(
    visual_commands: list[VisualCommand], cls: type[VisualCommand], **params
):
    try:
        cmd = cls(**params)
        visual_commands.append(cmd)
    except TypeError as ex:
        print("Bonk", ex)
    return params
