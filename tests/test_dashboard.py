"""The dashboard is plain HTML with no build step, so nothing type-checks the
field names it reads out of results.json. This does.
"""

import re
from pathlib import Path

import pytest

from core.results import DerivedFacts, Fit, RawFacts, ResultRow, RunContext

PAGE = Path(__file__).parent.parent / "dashboard" / "index.html"
SOURCE = PAGE.read_text(encoding="utf-8")

SECTIONS = {
    "raw": RawFacts,
    "derived": DerivedFacts,
    "fit": Fit,
}


def fields_used(section: str) -> set[str]:
    """Every `<something>.<section>.<field>` the page reads."""
    return set(re.findall(rf"\.{section}\.([a-z_]+)", SOURCE))


@pytest.mark.parametrize("section", sorted(SECTIONS))
def test_every_field_the_page_reads_exists_in_the_schema(section):
    known = set(SECTIONS[section].model_fields)
    used = fields_used(section)
    assert used, f"expected the page to read some {section} fields"
    assert used <= known, f"page reads unknown {section} fields: {sorted(used - known)}"


def test_the_run_context_fields_the_page_shows_all_exist():
    used = set(re.findall(r"\brun\.([a-z_]+)", SOURCE))
    assert used <= set(RunContext.model_fields), sorted(used - set(RunContext.model_fields))


def test_top_level_row_fields_exist():
    for field in ("rank", "title", "url"):
        assert f"row.{field}" in SOURCE or f"r.{field}" in SOURCE
        assert field in ResultRow.model_fields


# --- the things that actually broke ---------------------------------------


def test_the_page_declares_a_character_set():
    # http.server serves text/html with no charset, so without this the browser
    # guesses and every em dash renders as mojibake.
    assert '<meta charset="utf-8">' in SOURCE


def test_scraped_text_is_never_written_as_html():
    # Titles and reasons come off other people's pages. Assigning them to
    # innerHTML would make this an XSS hole in a page rendering untrusted input.
    assert not re.search(r"\.innerHTML\s*=", SOURCE)
    assert not re.search(r"insertAdjacentHTML|document\.write", SOURCE)
    assert "textContent" in SOURCE


def test_outbound_links_do_not_leak_the_opener():
    assert "noopener noreferrer" in SOURCE


def test_the_page_needs_no_network_dependencies():
    # A 90s page with a CDN in it is not a 90s page, and it breaks offline.
    assert "http://" not in SOURCE.replace("http://127.0.0.1", "")
    assert "https://" not in SOURCE
