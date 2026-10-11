"""Add, rename, remove, and list names in ``sim_profiles.yaml``.

The file in effect is ``<design>/models/sim_profiles.yaml`` when that file exists,
otherwise ``$BENCHGATE_HOME/config/sim_profiles.yaml``. A newly created local file
replaces the shared one for that design. Edits splice the named block and leave
the rest of the file, including comments, as they were.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from benchgate.paths import benchgate_home, resolve_design

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_KEY_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(.*)$")


class ProfileCatalogError(Exception):
    """The requested profile-name change cannot be done."""


def shared_catalog_path() -> Path:
    return benchgate_home() / "config" / "sim_profiles.yaml"


def local_catalog_path(design: Path | str) -> Path:
    return resolve_design(design) / "models" / "sim_profiles.yaml"


def resolve_catalog(
    design: Path | str | None,
    *,
    use_global: bool = False,
    use_local: bool = False,
    writing: bool = False,
) -> tuple[Path, str]:
    """Return ``(path, scope)`` where scope is ``shared`` or ``local``.

    A write aimed at a design that has no local file must say ``--global`` or
    ``--local``. Reading that design reports the shared file it actually uses.
    """
    if use_global and use_local:
        raise ProfileCatalogError("--global 和 --local 只能选一个")
    if use_local:
        if design is None:
            raise ProfileCatalogError("--local 需要 --design")
        design_path = resolve_design(design)
        if not (design_path / "models").is_dir():
            raise ProfileCatalogError(f"{design_path} 下没有 models/ 目录")
        return local_catalog_path(design_path), "local"
    if use_global or design is None:
        return shared_catalog_path(), "shared"

    local = local_catalog_path(design)
    if local.is_file():
        return local, "local"
    if writing:
        raise ProfileCatalogError(
            "这块板没有自己的 sim_profiles.yaml，仿真用的是本机共用文件。"
            "改共用文件请加 --global；只给这块板新建一份请加 --local。"
        )
    return shared_catalog_path(), "shared"


def check_name(name: str) -> str:
    if not _NAME_RE.match(name):
        raise ProfileCatalogError(f"名字只能包含字母、数字、下划线和连字符：{name}")
    return name


def split_profiles(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Split into the leading preamble and ``(name, block text)`` pairs.

    A block runs from its top-level key line up to, but not including, the next
    top-level key. Comments above a key stay with the previous block.
    """
    if text and not text.endswith("\n"):
        text += "\n"
    preamble: list[str] = []
    blocks: list[tuple[str, list[str]]] = []
    current_name: str | None = None
    current: list[str] = []
    started = False
    for line in text.splitlines(keepends=True):
        match = _KEY_LINE_RE.match(line) if not line.startswith((" ", "\t")) else None
        if match:
            if started and current_name is not None:
                blocks.append((current_name, current))
            elif not started:
                preamble = current
            current_name = match.group(1)
            current = [line]
            started = True
        else:
            current.append(line)
    if not started:
        return "".join(current), []
    if current_name is not None:
        blocks.append((current_name, current))
    return "".join(preamble), [(name, "".join(body)) for name, body in blocks]


def render_profiles(preamble: str, blocks: list[tuple[str, str]]) -> str:
    chunks: list[str] = []
    if preamble:
        chunks.append(preamble)
    for _name, body in blocks:
        chunks.append(body if body.endswith("\n") else body + "\n")
    text = "".join(chunks)
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def _load_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _names_from_yaml(text: str) -> list[str]:
    if not text.strip():
        return []
    data = yaml.safe_load(text)
    if data is None:
        return []
    if not isinstance(data, dict):
        raise ProfileCatalogError("sim_profiles.yaml 的顶层必须是一组名字")
    return [str(key) for key in data]


def read_blocks(path: Path) -> tuple[str, list[tuple[str, str]]]:
    text = _load_text(path)
    preamble, blocks = split_profiles(text)
    parsed = [name for name, _body in blocks]
    loaded = _names_from_yaml(text)
    if parsed != loaded:
        raise ProfileCatalogError(
            f"{path} 的顶层名字无法按原样修改（可能有重复键或特殊写法）。请直接编辑这个文件。"
        )
    return preamble, blocks


def list_profiles(path: Path) -> list[dict[str, str]]:
    """Return ``[{name, description}, ...]`` in file order. Missing file → empty."""
    if not path.is_file():
        return []
    _preamble, blocks = read_blocks(path)
    data = yaml.safe_load(_load_text(path)) or {}
    rows = []
    for name, _body in blocks:
        block = data.get(name) if isinstance(data, dict) else None
        description = ""
        if isinstance(block, dict) and block.get("description"):
            description = str(block["description"]).strip().splitlines()[0]
        rows.append({"name": name, "description": description})
    return rows


def show_profile(path: Path, name: str) -> str:
    check_name(name)
    _preamble, blocks = read_blocks(path)
    for block_name, body in blocks:
        if block_name == name:
            return body if body.endswith("\n") else body + "\n"
    raise ProfileCatalogError(f"{path} 里没有名字 {name}")


def _retarget(block: str, new_name: str) -> str:
    first, sep, rest = block.partition("\n")
    _old, colon, suffix = first.partition(":")
    renamed = f"{new_name}:{suffix}"
    return renamed + (sep + rest if sep else "")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _find_block(path: Path, name: str) -> str | None:
    if not path.is_file():
        return None
    _preamble, blocks = read_blocks(path)
    return next((body for block_name, body in blocks if block_name == name), None)


def add_profile(path: Path, name: str, source: str, *, source_path: Path | None = None) -> dict[str, str | bool]:
    """Copy ``source`` to ``name`` inside ``path``.

    The source is looked up in ``path`` first, then in ``source_path`` (the shared
    catalog), so ``--local`` can move a shared profile into a design under the
    same name. Creating the local file means the design stops seeing every other
    name in the shared catalog.
    """
    check_name(name)
    check_name(source)
    if name != source and _find_block(path, name) is not None:
        raise ProfileCatalogError(f"{path} 里已经有名字 {name}")
    if name == source and source_path is not None and source_path != path and _find_block(path, name) is not None:
        raise ProfileCatalogError(f"{path} 里已经有名字 {name}")
    found = _find_block(path, source) if path.is_file() else None
    origin = path
    if found is None and source_path is not None and source_path != path:
        found, origin = _find_block(source_path, source), source_path
    if found is None:
        where = path if source_path is None else f"{path} 或 {source_path}"
        raise ProfileCatalogError(f"{where} 里没有名字 {source}")
    if name == source and origin == path:
        raise ProfileCatalogError("新名字和来源名字相同")
    result: dict[str, str | bool] = {"path": str(path), "name": name, "source": source, "source_file": str(origin)}
    if not path.is_file():
        _write(path, render_profiles("", [(name, _retarget(found, name))]))
        return {**result, "created_file": True}
    preamble, blocks = read_blocks(path)
    if any(block_name == name for block_name, _body in blocks):
        raise ProfileCatalogError(f"{path} 里已经有名字 {name}")
    if blocks:
        prev_name, prev_body = blocks[-1]
        if not prev_body.endswith("\n\n"):
            prev_body = prev_body + ("\n" if prev_body.endswith("\n") else "\n\n")
            blocks[-1] = (prev_name, prev_body)
    if not found.endswith("\n"):
        found += "\n"
    blocks.append((name, _retarget(found, name)))
    _write(path, render_profiles(preamble, blocks))
    return {**result, "created_file": False}


def rename_profile(path: Path, old: str, new: str, *, force: bool = False) -> dict[str, str]:
    check_name(old)
    check_name(new)
    if old == "default" and not force:
        raise ProfileCatalogError("default 是找不到名字时的退路。改名请加 --force。")
    if old == new:
        raise ProfileCatalogError("新名字和原来相同")
    if not path.is_file():
        raise ProfileCatalogError(f"文件不存在：{path}")
    preamble, blocks = read_blocks(path)
    if any(block_name == new for block_name, _body in blocks):
        raise ProfileCatalogError(f"{path} 里已经有名字 {new}")
    renamed = False
    updated: list[tuple[str, str]] = []
    for block_name, body in blocks:
        if block_name == old:
            updated.append((new, _retarget(body, new)))
            renamed = True
        else:
            updated.append((block_name, body))
    if not renamed:
        raise ProfileCatalogError(f"{path} 里没有名字 {old}")
    _write(path, render_profiles(preamble, updated))
    return {"path": str(path), "old": old, "new": new}


def remove_profile(path: Path, name: str, *, force: bool = False) -> dict[str, str]:
    check_name(name)
    if name == "default" and not force:
        raise ProfileCatalogError("default 是找不到名字时的退路。删除请加 --force。")
    if not path.is_file():
        raise ProfileCatalogError(f"文件不存在：{path}")
    preamble, blocks = read_blocks(path)
    kept = [(block_name, body) for block_name, body in blocks if block_name != name]
    if len(kept) == len(blocks):
        raise ProfileCatalogError(f"{path} 里没有名字 {name}")
    _write(path, render_profiles(preamble, kept))
    return {"path": str(path), "name": name}


def git_repo_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def update_hook_profile(repo: Path, old: str, new: str | None) -> bool:
    """Rewrite ``--profile old`` in this repo's Cursor stop hook.

    ``new is None`` removes the flag so the next review uses the profile recorded
    on the design. Returns whether the file changed.
    """
    hooks_path = repo / ".cursor" / "hooks.json"
    if not hooks_path.is_file():
        return False
    try:
        config = json.loads(hooks_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileCatalogError(f"无法读取 {hooks_path}：{exc}") from exc
    stop = config.get("hooks", {}).get("stop", [])
    if not isinstance(stop, list):
        return False
    pattern = re.compile(rf"--profile\s+{re.escape(old)}(?=\s|$)")
    changed = False
    for entry in stop:
        if not isinstance(entry, dict):
            continue
        command = str(entry.get("command", ""))
        if "benchgate" not in command or "hook stop" not in command:
            continue
        if not pattern.search(command):
            continue
        if new is None:
            entry["command"] = re.sub(rf"\s+--profile\s+{re.escape(old)}(?=\s|$)", "", command)
        else:
            entry["command"] = pattern.sub(f"--profile {new}", command, count=1)
        changed = True
    if changed:
        hooks_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return changed
