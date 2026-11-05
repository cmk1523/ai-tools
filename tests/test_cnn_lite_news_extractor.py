"""
title: CNN Lite News Extractor tests
description: Tests for headline, URL, and date CSV extraction from CNN Lite HTML files.
requirements: pytest
version: 0.1.0
"""

import csv
import importlib.util
import io
import re
from pathlib import Path

import pytest


_MODULE_PATH = Path(__file__).resolve().parents[1] / "cnn-lite-news-extractor.py"
_SPEC = importlib.util.spec_from_file_location("cnn_lite_news_extractor", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

Tools = _MODULE.Tools
_SAMPLE = Path(__file__).resolve().parent / "download-2026-11-05.htm"
_URL_DATE_RE = re.compile(r"/(\d{4})/(\d{2})/(\d{2})(?:/|$)")


def _write_page(directory: Path, name: str, html: str) -> Path:
    page = directory / name
    page.write_text(html, encoding="utf-8")
    return page


def _rows(csv_text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(csv_text)))


def test_sample_page_exports_every_story_as_csv():
    csv_text = Tools().extract_headlines(str(_SAMPLE))
    rows = _rows(csv_text)

    assert csv_text.startswith("headline,url,date\n")
    assert rows[0] == ["headline", "url", "date"]
    assert len(rows) == 101
    assert all(len(row) == 3 and row[0] and row[1] for row in rows[1:])

    assert rows[1] == [
        "Tracking the House: See which races are still counting votes",
        "https://www.cnn.com/politics/house-races-midterms-votes-vis",
        "2026-11-05",
    ]
    assert rows[4] == [
        "Republicans quickly turn against Pete Hegseth after midterms rout",
        "https://www.cnn.com/2026/11/04/politics/pete-hegseth-impeachment-trump",
        "2026-11-04",
    ]

    for headline, url, story_date in rows[1:]:
        assert url.startswith("https://www.cnn.com/")
        match = _URL_DATE_RE.search(url)
        if match:
            assert story_date == "-".join(match.groups())
        else:
            assert story_date == "2026-11-05"
        assert headline != "Go to the full CNN experience"
        assert "\u2018" not in headline
        assert "\u2019" not in headline

    water = next(line for line in csv_text.splitlines() if "Water rescues" in line)
    assert water.startswith('"')
    assert water.endswith(
        ",https://www.cnn.com/2026/11/05/weather/flooding-georgia-tennessee-mccaysville-copperhill-climate,2026-11-05"
    )


def test_page_date_formats_quoting_and_url_fallback(tmp_path: Path):
    page = _write_page(
        tmp_path,
        "lite.HTML",
        """
        <header class="header--lite"><span>11/5/2026</span></header>
        <ul>
          <li class="card--lite">
            <a href="/politics/no-date">
              Headline, with comma and a "quote"
            </a>
          </li>
          <li class="card--lite">
            <a href="/2026/11/04/politics/dated-story">Dated story</a>
          </li>
          <li class="card--lite">
            <a href="/2026/13/40/politics/bad-date">Bad date falls back</a>
          </li>
          <li class="card--lite">
            <a href="https://edition.cnn.com/2026/10/02/world/already-absolute">
              Absolute link
            </a>
          </li>
          <li class="card--lite"><a href="javascript:void(0)">Skip me</a></li>
        </ul>
        """,
    )

    csv_text = Tools().extract_headlines(str(page))
    assert _rows(csv_text) == [
        ["headline", "url", "date"],
        [
            'Headline, with comma and a "quote"',
            "https://www.cnn.com/politics/no-date",
            "2026-11-05",
        ],
        [
            "Dated story",
            "https://www.cnn.com/2026/11/04/politics/dated-story",
            "2026-11-04",
        ],
        [
            "Bad date falls back",
            "https://www.cnn.com/2026/13/40/politics/bad-date",
            "2026-11-05",
        ],
        [
            "Absolute link",
            "https://edition.cnn.com/2026/10/02/world/already-absolute",
            "2026-10-02",
        ],
    ]
    assert '""quote""' in csv_text


def test_iso_page_date_and_date_before_the_header_class(tmp_path: Path):
    iso_page = _write_page(
        tmp_path,
        "iso.htm",
        """
        <header class="header--lite"><span>2026-11-05</span></header>
        <li class="card--lite"><a href="/travel/retreat">Retreat</a></li>
        """,
    )
    assert _rows(Tools().extract_headlines(str(iso_page)))[1][2] == "2026-11-05"

    loose_page = _write_page(
        tmp_path,
        "loose.html",
        """
        <p>Saved 11/5/2026</p>
        <li class="card--lite"><a href="/politics/story">No path date</a></li>
        """,
    )
    rows = _rows(Tools().extract_headlines(str(loose_page)))
    assert rows[1] == [
        "No path date",
        "https://www.cnn.com/politics/story",
        "2026-11-05",
    ]


def test_url_date_is_enough_when_the_page_has_no_date(tmp_path: Path):
    page = _write_page(
        tmp_path,
        "dated-only.html",
        '<li class="card--lite"><a href="/2026/09/12/us/example">Example</a></li>',
    )
    rows = _rows(Tools().extract_headlines(str(page)))
    assert rows[1][2] == "2026-09-12"


def test_curly_single_quotes_become_straight_apostrophes(tmp_path: Path):
    page = _write_page(
        tmp_path,
        "quotes.htm",
        """
        <header class="header--lite"><span>2026-11-05</span></header>
        <li class="card--lite">
          <a href="/2026/11/05/us/quotes">Trump\u2019s \u2018mandate\u2019</a>
        </li>
        """,
    )
    rows = _rows(Tools().extract_headlines(str(page)))
    assert rows[1][0] == "Trump's 'mandate'"


def test_missing_date_empty_file_and_rejected_paths(tmp_path: Path):
    undated = _write_page(
        tmp_path,
        "undated.html",
        '<li class="card--lite"><a href="/politics/story">No date here</a></li>',
    )
    with pytest.raises(ValueError, match="No date near the top"):
        Tools().extract_headlines(str(undated))

    empty = _write_page(tmp_path, "empty.htm", "<html></html>")
    assert Tools().extract_headlines(str(empty)) == "headline,url,date\n"

    text_file = _write_page(tmp_path, "notes.txt", "<html></html>")
    with pytest.raises(ValueError, match=r"\.htm or \.html"):
        Tools().extract_headlines(str(text_file))
    with pytest.raises(FileNotFoundError):
        Tools().extract_headlines(str(tmp_path / "missing.htm"))
    with pytest.raises(ValueError, match="not a file"):
        Tools().extract_headlines(str(tmp_path))
    with pytest.raises(ValueError, match="must not be empty"):
        Tools().extract_headlines("   ")
    with pytest.raises(TypeError):
        Tools().extract_headlines(None)  # type: ignore[arg-type]


def test_cli_prints_csv_and_reports_usage(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    page = _write_page(
        tmp_path,
        "cli.htm",
        """
        <header class="header--lite"><span>11/5/2026</span></header>
        <li class="card--lite"><a href="/politics/story">CLI story</a></li>
        """,
    )

    assert _MODULE.main([str(page)]) == 0
    captured = capsys.readouterr()
    assert _rows(captured.out)[1][0] == "CLI story"
    assert captured.err == ""

    assert _MODULE.main([]) == 2
    assert "Usage:" in capsys.readouterr().err
