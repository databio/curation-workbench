"""The one YAML writer for hand-curated project files.

Every writer of schema YAML, curation YAML or other hand-curated YAML
files goes through `load()` / `dump()` here, so a
programmatic edit produces a diff containing only its own change.

Two block-sequence styles exist in the tree, and each file keeps its own:

  indented  a sequence item sits deeper than its parent key
                imports:
                  - linkml:types
            written with indent(mapping=2, sequence=4, offset=2), width 4096
  flush     a sequence item sits at the same column as its parent key
                imports:
                - linkml:types
            written with ruamel's default indents, width 120

A file is "indented" if ANY block sequence item under a mapping key is indented
deeper than the key. A project format check (e.g. `fmt --check`) keeps
every `src/*.yaml` a fixed point of load + dump.

Nulls: ruamel writes None as an empty value. A file that spells nulls out
(`key: null`, e.g. decisions.yaml) keeps `null`; files that
use empty values (`src/*.yaml` permissible values) keep those. A writer may set
`Doc.nulls` to choose explicitly.
"""
import io
import os
import re
import tempfile
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.representer import RoundTripRepresenter

_KEY = re.compile(r"^( *)[^\s#-][^:#]*:\s*(#.*)?$")
_ITEM = re.compile(r"^( *)- ")
_NULL = re.compile(r"^[^#]*(:|-)\s+null\s*(#.*)?$", re.M)


def is_indented(text):
    """True if any block sequence item is indented deeper than its parent key."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _KEY.match(line)
        if not m:
            continue
        for nxt in lines[i + 1:]:
            if not nxt.strip() or nxt.lstrip().startswith("#"):
                continue
            item = _ITEM.match(nxt)
            if item and len(item.group(1)) > len(m.group(1)):
                return True
            break
    return False


def uses_null(text):
    """True if the file spells null values as `null` rather than leaving them empty."""
    return bool(_NULL.search(text))


class _NullRepresenter(RoundTripRepresenter):
    """Round-trip representer that writes None as `null` (a subclass, so the
    default representer used for every other file is left alone)."""


_NullRepresenter.add_representer(
    type(None), lambda r, _: r.represent_scalar("tag:yaml.org,2002:null", "null"))


def _yaml(indented, nulls=False):
    y = YAML()
    y.preserve_quotes = True
    if nulls:
        y.Representer = _NullRepresenter
    if indented:
        y.indent(mapping=2, sequence=4, offset=2)
        y.width = 4096
    else:
        y.width = 120
    return y


class Doc:
    """A loaded document plus the style it must be written back in."""

    def __init__(self, data, indented, nulls=False):
        self.data = data
        self.indented = indented
        self.nulls = nulls


def load(path):
    """Round-trip load `path`; returns a Doc (use `.data` for the content)."""
    text = Path(path).read_text()
    indented = is_indented(text)
    return Doc(_yaml(indented).load(text), indented, uses_null(text))


def dumps(doc):
    buf = io.StringIO()
    _yaml(doc.indented, doc.nulls).dump(doc.data, buf)
    return buf.getvalue()


def dump(doc, path):
    """Write `doc` to `path` atomically (temp file in the same dir + os.replace)."""
    path = Path(path)
    text = dumps(doc)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
