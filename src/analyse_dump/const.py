from __future__ import annotations

# Language enum
LANG_JS = 0
LANG_KOTLIN = 1

# Edge type enum
EDGE_UNKNOWN = 0
EDGE_FIELD = 1
EDGE_ARRAY_ELEMENT = 2
EDGE_PROPERTY = 3
EDGE_ELEMENT = 4
EDGE_HIDDEN = 5
EDGE_SHORTCUT = 6
EDGE_WEAK = 7
EDGE_INTERNAL = 8

EDGE_TYPE_BY_NAME = {
    "field": EDGE_FIELD,
    "array_element": EDGE_ARRAY_ELEMENT,
    "property": EDGE_PROPERTY,
    "element": EDGE_ELEMENT,
    "hidden": EDGE_HIDDEN,
    "shortcut": EDGE_SHORTCUT,
    "weak": EDGE_WEAK,
    "internal": EDGE_INTERNAL,
}

# Edge label kind enum
NAME_KIND_NONE = 0
NAME_KIND_STRING_INDEX = 1
NAME_KIND_ARRAY_INDEX = 2
NAME_KIND_FIELD_NAME = 3

# Ref kind enum
REF_KIND_UNKNOWN = 0
REF_KIND_STABLE_REF = 1
REF_KIND_NAPI_REF = 2

REF_KIND_BY_NAME = {
    "stable_ref": REF_KIND_STABLE_REF,
    "napi_ref": REF_KIND_NAPI_REF,
}
