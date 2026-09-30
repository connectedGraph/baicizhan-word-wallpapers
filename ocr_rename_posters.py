"""Use OCR to extract the English headword from 百词斩 posters.

The command is intentionally two-phase:

1. Scan images and write a machine-readable review file.
2. Rename only after ``--apply`` is supplied.

The original ``poster_*.png`` files are used as the input set so that running
the command again is safe after a previous rename.  Renames happen through
temporary names, which also makes name collisions deterministic and avoids
overwriting an existing image.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


WORD_RE = re.compile(r"[a-zA-Z]{3,24}")
IMAGE_RE = re.compile(r"^poster_.*\.(?:png|jpg|jpeg)$", re.IGNORECASE)

_WORKER_OCR: Any = None


@dataclass
class RawCandidate:
    text: str
    score: float
    raw_score: float
    height: float
    width: float
    source: str


@dataclass
class Recognition:
    path: str
    raw_text: str
    normalized: str
    word: str
    confidence: float
    box_height: float
    source: str
    status: str
    alternatives: list[str]


def _box_size(box: Any) -> tuple[float, float]:
    points = [(float(point[0]), float(point[1])) for point in box]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return max(xs) - min(xs), max(ys) - min(ys)


def _tokens(text: str) -> list[str]:
    """Extract possible English words while retaining the OCR text context."""

    return [token.lower() for token in WORD_RE.findall(text)]


def _ocr_result(ocr: Any, image: Any, source: str) -> list[RawCandidate]:
    result, _ = ocr(image)
    if not result:
        return []

    candidates: list[RawCandidate] = []
    for box, text, score in result:
        width, height = _box_size(box)
        for token in _tokens(str(text)):
            # Pronunciation lines commonly contain brackets, apostrophes, or
            # digits.  They are retained as alternatives but ranked below the
            # large headword by this penalty.
            punctuation_penalty = 0.55 if any(char in str(text) for char in "[]()/'") else 1.0
            size_bonus = min(height / 120.0, 2.5)
            rank = float(score) * max(size_bonus, 0.35) * punctuation_penalty
            candidates.append(
                RawCandidate(
                    text=token,
                    score=rank,
                    raw_score=float(score),
                    height=height,
                    width=width,
                    source=source,
                )
            )
    return candidates


def _scan_one(path_text: str) -> dict[str, Any]:
    """Worker entry point.  Each process owns one OCR model instance."""

    global _WORKER_OCR
    if _WORKER_OCR is None:
        from rapidocr_onnxruntime import RapidOCR

        _WORKER_OCR = RapidOCR()

    import cv2

    path = Path(path_text)
    all_candidates = _ocr_result(_WORKER_OCR, str(path), "original")

    def best_score(items: Iterable[RawCandidate]) -> float:
        return max((item.score for item in items), default=0.0)

    # Decorative fonts and low-contrast posters often benefit from a cheap
    # grayscale pass.  Do these extra passes only when the original scan is
    # not convincing, keeping the normal batch reasonably fast.
    if not all_candidates or best_score(all_candidates) < 0.75:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is not None:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)
            all_candidates.extend(_ocr_result(_WORKER_OCR, enhanced, "gray"))

            _, threshold = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            all_candidates.extend(_ocr_result(_WORKER_OCR, threshold, "threshold"))

    return {"path": path_text, "candidates": [asdict(item) for item in all_candidates]}


def _load_dictionary() -> tuple[set[str], Any, Any]:
    """Return a spelling dictionary and fuzzy matching helpers if available."""

    try:
        from rapidfuzz import fuzz, process
        from wordfreq import top_n_list, zipf_frequency

        words = {
            word.lower()
            for word in top_n_list("en", 200_000)
            if re.fullmatch(r"[a-z]{3,24}", word.lower())
        }
        common_by_length: dict[int, list[str]] = {}
        prefix_map: dict[str, list[str]] = {}
        for word in words:
            if zipf_frequency(word, "en") >= 2.5:
                common_by_length.setdefault(len(word), []).append(word)
            if len(word) >= 4:
                prefix_map.setdefault(word[1:], []).append(word)
        return words, (process, fuzz, common_by_length, prefix_map), zipf_frequency
    except ImportError:
        return set(), None, None


def _pick_candidate(items: list[RawCandidate]) -> RawCandidate | None:
    if not items:
        return None
    # De-duplicate repeated OCR outputs while keeping the strongest box.
    strongest: dict[str, RawCandidate] = {}
    for item in items:
        old = strongest.get(item.text)
        if old is None or item.score > old.score:
            strongest[item.text] = item
    return max(strongest.values(), key=lambda item: (item.score, item.height, len(item.text)))


def _repair_word(raw: str, dictionary: set[str], fuzzy: Any, zipf_frequency: Any) -> tuple[str, str, list[str]]:
    """Repair obvious OCR spelling damage without inventing a low-confidence word."""

    raw = raw.lower()
    if not raw:
        return "", "review", []

    if raw in dictionary:
        # A missing leading glyph is a recurring failure mode in these
        # posters.  Prefer a one-letter prefix completion only when it is a
        # substantially more common word (rochet -> crochet is the typical
        # case); otherwise preserve a valid OCR word such as burgeon.
        raw_frequency = zipf_frequency(raw, "en") if zipf_frequency else 0.0
        prefix_matches = [
            word for word in (fuzzy[3].get(raw, []) if fuzzy else []) if len(word) == len(raw) + 1
        ]
        if zipf_frequency and prefix_matches:
            best_prefix = max(prefix_matches, key=lambda word: zipf_frequency(word, "en"))
            if zipf_frequency(best_prefix, "en") >= raw_frequency + 1.0:
                return best_prefix, "repaired", [best_prefix, raw]
        # Very short, low-frequency strings are often abbreviations or
        # background fragments.  Keep them visible for review rather than
        # treating a dictionary hit as proof.
        if len(raw) <= 4 and raw_frequency < 3.0:
            return raw, "review", [raw]
        return raw, "ok", [raw]

    if dictionary and fuzzy and zipf_frequency:
        process, fuzz, common_by_length, _ = fuzzy
        # A title is generally only missing or confusing a small number of
        # glyphs.  Limit the search to words of nearby length.
        pool = []
        for length in range(max(3, len(raw) - 2), len(raw) + 3):
            pool.extend(common_by_length.get(length, []))
        matches = process.extract(raw, pool, scorer=fuzz.ratio, limit=8)
        alternatives = [match[0] for match in matches]
        if matches:
            best_word, similarity, _ = matches[0]
            # Require a fairly close spelling and a real English frequency.
            # The 0.80 threshold covers examples such as oowder -> powder,
            # while rejecting most background fragments.
            if similarity >= 80 and zipf_frequency(best_word, "en") >= 2.0:
                return best_word, "repaired", alternatives
        return raw, "review", alternatives

    # The OCR result is still useful without optional spelling packages.  It
    # can be reviewed manually and is accepted only when it is already a
    # clean English-looking token.
    return raw, "review", []


def _recognize(path: Path, scan: dict[str, Any], dictionary: set[str], fuzzy: Any, zipf_frequency: Any) -> Recognition:
    candidates = [RawCandidate(**candidate) for candidate in scan.get("candidates", [])]
    candidate = _pick_candidate(candidates)
    if candidate is None:
        return Recognition(str(path), "", "", "", 0.0, 0.0, "", "review", [])

    word, status, alternatives = _repair_word(candidate.text, dictionary, fuzzy, zipf_frequency)
    confidence = min(max(candidate.raw_score, 0.0), 1.0)
    if status == "repaired" and candidate.height < 70:
        status = "review"
    if confidence < 0.45 or candidate.height < 45:
        status = "review"
    return Recognition(
        path=str(path),
        raw_text=candidate.text,
        normalized=candidate.text.lower(),
        word=word,
        confidence=confidence,
        box_height=candidate.height,
        source=candidate.source,
        status=status,
        alternatives=alternatives,
    )


def _write_reports(records: list[Recognition], report_path: Path, csv_path: Path) -> None:
    report_path.write_text(
        json.dumps([asdict(record) for record in records], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "path",
                "raw_text",
                "normalized",
                "word",
                "confidence",
                "box_height",
                "source",
                "status",
                "alternatives",
            ],
        )
        writer.writeheader()
        for record in records:
            row = asdict(record)
            row["alternatives"] = ", ".join(record.alternatives)
            writer.writerow(row)


def _target_names(records: list[Recognition]) -> dict[str, Path]:
    """Build deterministic names and reserve collisions with suffixes."""

    used: set[str] = set()
    targets: dict[str, Path] = {}
    for record in records:
        if not record.word:
            continue
        stem = re.sub(r"[^a-z0-9-]+", "-", record.word.lower()).strip("-")
        if not stem:
            continue
        suffix = Path(record.path).suffix.lower()
        base = stem
        counter = 1
        while f"{stem}{suffix}" in used or (Path(record.path).with_name(f"{stem}{suffix}").exists() and Path(record.path).name.lower() != f"{stem}{suffix}"):
            counter += 1
            stem = f"{base}-{counter}"
        filename = f"{stem}{suffix}"
        used.add(filename)
        targets[record.path] = Path(record.path).with_name(filename)
    return targets


def _apply_renames(records: list[Recognition], include_review: bool) -> int:
    eligible = [
        record
        for record in records
        if record.word and (include_review or record.status in {"ok", "repaired", "manual"})
    ]
    targets = _target_names(eligible)
    moves: list[tuple[Path, Path]] = []
    for record in eligible:
        source = Path(record.path)
        target = targets.get(record.path)
        if target is None or source == target:
            continue
        moves.append((source, target))

    if not moves:
        return 0

    temp_moves: list[tuple[Path, Path]] = []
    token = uuid.uuid4().hex
    for index, (source, target) in enumerate(moves):
        temporary = source.with_name(f".__ocr_rename_{token}_{index}{source.suffix}")
        source.rename(temporary)
        temp_moves.append((temporary, target))
    for temporary, target in temp_moves:
        temporary.rename(target)
    return len(moves)


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR 百词斩单词海报并按单词命名图片")
    parser.add_argument("--input", type=Path, default=Path("1"), help="图片目录，默认：1")
    parser.add_argument("--workers", type=int, default=1, help="OCR 进程数；CPU 较快时可设为 2-4")
    parser.add_argument("--apply", action="store_true", help="实际执行重命名；不带此参数只生成报告")
    parser.add_argument("--include-review", action="store_true", help="连 review 状态的可疑候选也重命名")
    parser.add_argument("--report", type=Path, default=Path("ocr_rename_report.json"))
    parser.add_argument("--csv", type=Path, default=Path("ocr_rename_review.csv"))
    parser.add_argument("--cache", type=Path, default=Path("ocr_rename_scan_cache.json"))
    parser.add_argument("--overrides", type=Path, default=Path("ocr_rename_overrides.json"))
    args = parser.parse_args()

    if not args.input.is_dir():
        parser.error(f"图片目录不存在：{args.input}")

    files = sorted(
        path for path in args.input.iterdir() if path.is_file() and IMAGE_RE.match(path.name)
    )
    if not files:
        print("没有找到 poster_*.png/jpg 图片。")
        return 0

    dictionary, fuzzy, zipf_frequency = _load_dictionary()
    if not dictionary:
        print("提示：未安装 wordfreq/rapidfuzz，拼写纠错将保守运行。", file=sys.stderr)

    worker_count = max(1, args.workers)
    print(f"扫描 {len(files)} 张海报，workers={worker_count} ...")
    if worker_count == 1:
        cache: dict[str, Any] = {}
        if args.cache.exists():
            try:
                cache = json.loads(args.cache.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cache = {}
        scans = []
        for index, path in enumerate(files, start=1):
            cache_key = str(path.resolve())
            stat = path.stat()
            cached = cache.get(cache_key)
            if cached and cached.get("mtime_ns") == stat.st_mtime_ns and cached.get("size") == stat.st_size:
                scan = cached["scan"]
            else:
                scan = _scan_one(str(path))
                cache[cache_key] = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size, "scan": scan}
            scans.append(scan)
            if index == 1 or index % 10 == 0 or index == len(files):
                print(f"  {index}/{len(files)}", flush=True)
            if index % 10 == 0:
                args.cache.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        args.cache.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    else:
        with ProcessPoolExecutor(max_workers=worker_count) as pool:
            scans = list(pool.map(_scan_one, [str(path) for path in files]))

    records = [
        _recognize(path, scan, dictionary, fuzzy, zipf_frequency)
        for path, scan in zip(files, scans)
    ]
    if args.overrides.exists():
        try:
            overrides = json.loads(args.overrides.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            overrides = {}
        for record in records:
            override = overrides.get(Path(record.path).name)
            if override:
                record.word = str(override).lower()
                record.status = "manual"
                record.alternatives = [record.word]
    _write_reports(records, args.report, args.csv)

    counts: dict[str, int] = {}
    for record in records:
        counts[record.status] = counts.get(record.status, 0) + 1
    print("识别结果：" + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    print(f"报告：{args.report}")
    print(f"复核表：{args.csv}")

    if args.apply:
        renamed = _apply_renames(records, args.include_review)
        print(f"已重命名 {renamed} 张；无法得到单词的图片保留原名。")
    else:
        print("当前为 dry-run；确认报告后加 --apply 执行重命名。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
