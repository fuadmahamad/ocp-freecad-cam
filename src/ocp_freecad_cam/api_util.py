import io
from typing import Literal, Optional, TypeAlias, Union

import FreeCAD
import Path.Base.Util as PathUtil
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepTools import BRepTools
from OCP.gp import gp_Pnt, gp_Trsf
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_ShapeEnum
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import (
    TopoDS_Builder,
    TopoDS_Compound,
    TopoDS_Edge,
    TopoDS_Face,
    TopoDS_Shape,
    TopoDS_Solid,
    TopoDS_Vertex,
    TopoDS_Wire,
)

try:
    import cadquery as cq
except ImportError:
    cq = None
try:
    import build123d as b3d
except ImportError:
    b3d = None

TopoDS_ShapeTypes: TypeAlias = Union[
    TopoDS_Face, TopoDS_Wire, TopoDS_Edge, TopoDS_Vertex, TopoDS_Compound
]
CompoundSource: TypeAlias = Union[
    TopoDS_Compound,
    "cq.Compound",
    "b3d.Compound",
    "cq.Workplane",
    "b3d.ShapeList",
    "cq.Solid",
    "b3d.Solid",
]
ShapeSource: TypeAlias = Union[
    TopoDS_ShapeTypes,
    "cq.Workplane",
    "cq.Face",
    "cq.Wire",
    "cq.Edge",
    "cq.Vertex",
    "cq.Compound",
    "b3d.ShapeList",
    "b3d.Face",
    "b3d.Wire",
    "b3d.Edge",
    "b3d.Vertex",
    "b3d.Compound",
]
ShapeSourceOrIterable: TypeAlias = Union[ShapeSource, list[ShapeSource]]


# todo wire needs to be broken to edges..


def extract_topods_shapes(
    shape_source: ShapeSourceOrIterable, compound=False
) -> list[TopoDS_ShapeTypes]:
    if isinstance(shape_source, list):
        shapes = []
        for source in shape_source:
            shapes += extract_topods_shapes(source)
        return shapes

    if cq:
        valid_cq_shapes = (
            [cq.Compound, cq.Solid]
            if compound
            else [cq.Face, cq.Wire, cq.Edge, cq.Vertex]
        )
        if isinstance(shape_source, cq.Workplane):
            return [
                shape.wrapped
                for shape in shape_source.objects
                if type(shape) in valid_cq_shapes
            ]
        elif type(shape_source) in valid_cq_shapes:
            return [shape_source.wrapped]
    if b3d:
        valid_b3d_shapes = (
            [b3d.Compound, b3d.Solid] if compound else [b3d.Face, b3d.Wire, b3d.Vertex]
        )
        if isinstance(shape_source, b3d.ShapeList):
            return [
                shape.wrapped
                for shape in shape_source
                if type(shape) in valid_b3d_shapes
            ]
        elif type(shape_source) in valid_b3d_shapes:
            return [shape_source.wrapped]

    valid_topods_shapes = (
        [TopoDS_Compound, TopoDS_Solid]
        if compound
        else [TopoDS_Face, TopoDS_Wire, TopoDS_Edge, TopoDS_Vertex]
    )
    if type(shape_source) in valid_topods_shapes:
        return [shape_source]

    raise ValueError(f"Unknown shape source of type {type(shape_source)}")


def split_shapes_by_type(
    shapes: list[TopoDS_ShapeTypes],
) -> tuple[list[TopoDS_Face], list[TopoDS_Edge], list[TopoDS_Vertex]]:
    faces = []
    wires = []
    edges = []
    vertices = []
    for shape in shapes:
        if isinstance(shape, TopoDS_Face):
            faces.append(shape)
        elif isinstance(shape, TopoDS_Wire):
            wires.append(shape)
        elif isinstance(shape, TopoDS_Edge):
            edges.append(shape)
        elif isinstance(shape, TopoDS_Vertex):
            vertices.append(shape)
        elif isinstance(shape, TopoDS_Compound):
            faces += break_shape_to(shape, TopAbs_FACE)
        else:
            raise ValueError(f"Unknown shape type {type(shape)}")

    # Selecting wires is not supported by FreeCAD so explode wires
    # into edges
    for wire in wires:
        wire_edges = break_shape_to(wire, TopAbs_EDGE)
        edges += wire_edges

    return faces, edges, vertices


def break_shape_to(
    shape: TopoDS_Shape, shape_type: TopAbs_ShapeEnum
) -> list[TopoDS_Shape]:
    sub_shapes = []
    explorer = TopExp_Explorer(shape, shape_type)
    while explorer.More():
        sub_shape = explorer.Current()
        sub_shapes.append(sub_shape)
        explorer.Next()
    return sub_shapes


def transform_shapes(shapes: list[TopoDS_Shape], trsf: gp_Trsf) -> list[TopoDS_Shape]:
    return [transform_shape(shape, trsf) for shape in shapes]


def transform_shape(shape: TopoDS_Shape, trsf: gp_Trsf) -> TopoDS_Shape:
    return BRepBuilderAPI_Transform(shape, trsf).Shape()


def shapes_to_brep(shapes: list[TopoDS_Shape]):
    return [shape_to_brep(shape) for shape in shapes]


def scale_shape(shape: TopoDS_Shape, scale_factor: float) -> TopoDS_Shape:
    trsf = gp_Trsf()
    center_of_the_universe = gp_Pnt(0, 0, 0)
    trsf.SetScale(center_of_the_universe, scale_factor)
    return transform_shape(shape, trsf)


def shape_to_brep(shape: TopoDS_Shape):
    data = io.BytesIO()
    BRepTools.Write_s(shape, data)
    data.seek(0)
    return data.read().decode("utf8")


def shape_source_to_compound_brep(
    shape_source: ShapeSourceOrIterable,
    trsf: gp_Trsf,
    scale_factor: Optional[float],
    allow_none=False,
):
    if allow_none and shape_source is None:
        return {
            "face_count": 0,
            "edge_count": 0,
            "vertex_count": 0,
            "compound_brep": None,
        }

    shapes = extract_topods_shapes(shape_source)
    if not shapes:
        shapes = extract_topods_shapes(shape_source, True)
    faces, edges, vertices = split_shapes_by_type(shapes)

    if not faces and not edges and not vertices:
        raise ValueError("Empty ShapeSource")

    compound = TopoDS_Compound()
    builder = TopoDS_Builder()
    builder.MakeCompound(compound)

    for face in faces:
        builder.Add(compound, face)

    for edge in edges:
        builder.Add(compound, edge)

    for vertex in vertices:
        builder.Add(compound, vertex)

    compound = transform_shape(compound, trsf)
    if scale_factor:
        compound = scale_shape(compound, scale_factor)

    return {
        "face_count": len(faces),
        "edge_count": len(edges),
        "vertex_count": len(vertices),
        "compound_brep": shape_to_brep(transform_shape(compound, trsf)),
    }


class AutoUnitKey:
    def __init__(self, key):
        self.key = key


class AutoUnitValue:
    def __init__(self, value):
        self.value = value

    def convert(self, unit: Literal["metric", "imperial"]):
        return self._convert(self.value, unit)

    def _convert(self, value, unit: Literal["metric", "imperial"]):
        pq = FreeCAD.Units.parseQuantity

        if isinstance(value, (int, float)):
            if unit == "metric":
                return value
            if unit == "imperial":
                return float(pq(f"{value} in"))
            raise ValueError(f"Unknown unit: {unit}")

        elif isinstance(value, tuple):
            return tuple(self._convert(v, unit) for v in value)

        return float(pq(value))


ParamMapping: TypeAlias = dict[str, Union[str, AutoUnitKey, dict[str, str]]]


def map_prop(mapping: ParamMapping, k, v):
    result = mapping[k]
    match result:
        case AutoUnitKey():
            return result.key, AutoUnitValue(v)
        case (nk, dv):
            return nk, dv[v]
        case nk:
            return nk, v


def map_params(mapping: ParamMapping, **kwargs):
    return dict(map_prop(mapping, k, v) for k, v in kwargs.items() if v is not None)


def apply_params(fc_obj, params, unit: Literal["metric", "imperial"]):
    for k, v in params.items():
        if isinstance(v, AutoUnitValue):
            v = v.convert(unit)
        PathUtil.setProperty(fc_obj, k, v)
