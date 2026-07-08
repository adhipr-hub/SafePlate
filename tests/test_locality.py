from safeplate.extraction2 import locality as L

SANTA_MONICA = ("https://www.sweetmaplesf.com/files/"
                "02-28-2026-sweet-maple-santa-monica-menu-02-27-2026-pdf.pdf")
CUPERTINO_ADDR = "20010 Stevens Creek Blvd, Cupertino, CA 95014, USA"


def test_city_from_address():
    assert L.city_from_address(CUPERTINO_ADDR) == "cupertino"
    assert L.city_from_address("Palo Alto, CA") == "palo-alto"
    assert L.city_from_address("") is None
    assert L.city_from_address("SingleField") is None


def test_source_city_slug_extracts_place_dropping_name_and_descriptors():
    assert L.source_city_slug(SANTA_MONICA, "Sweet Maple") == "santa-monica"
    assert L.source_city_slug(
        "https://x.com/menu-cupertino", "Sweet Maple") == "cupertino"
    # pure descriptor filename -> no city
    assert L.source_city_slug("https://x.com/files/dinner-menu.pdf", "Sweet Maple") is None


def test_url_has_city():
    assert L.url_has_city("https://x.com/menu-cupertino", "cupertino") is True
    assert L.url_has_city(SANTA_MONICA, "cupertino") is False


def test_menu_city_mismatch():
    # wrong-location PDF for a Cupertino diner -> mismatch
    assert L.menu_city_mismatch(SANTA_MONICA, CUPERTINO_ADDR, "Sweet Maple") is True
    # the diner's own city menu -> not a mismatch
    assert L.menu_city_mismatch(
        "https://www.sweetmaplesf.com/menu-cupertino", CUPERTINO_ADDR, "Sweet Maple") is False
    # unreadable city -> never assert a mismatch
    assert L.menu_city_mismatch("https://x.com/files/menu.pdf", CUPERTINO_ADDR, "Sweet Maple") is False
    assert L.menu_city_mismatch(SANTA_MONICA, "", "Sweet Maple") is False


# --- In-document locality contradiction (off-site web-search documents) -------
# Regression: extracting Cicero's Pizza (San Jose CA), the Brave menu-PDF fallback
# accepted an aggregator PDF for the SAME-NAME restaurant in Interlochen MI
# (wnam-cdn.menuweb.menu/.../ciceros-pizza-parlor-interlochen-menu.pdf) and merged
# its items; its Australian header prose also triggered the from-Australia notice.
# The fixture below is an abridged copy of that PDF's REAL extracted text.

CICEROS_SJ_ADDR = "6138 Bollinger Rd, San Jose, CA 95129, USA"
CICEROS_INTERLOCHEN_PDF_TEXT = (
    "Smashing Sorrento Menu\n"
    "119/125 Ocean Beach Rd, Sorrento, Victoria, Australia, 3943, SORRENTO\n"
    "+61359845897 - http://www.smashingsorrento.com.au\n"
    "https://menuweb.menu\n"
    "Pizza\nFIVE MEAT\n$15.2\nCHEESE PIZZA\nPEPPERONI PIZZA\n"
    "Cicero's Pizza Menu\n"
    "Cicero's Pizza\n"
    "2408 M 137, Interlochen, United\nStates\n"
    "Made with Menu\nOpening Hours:\nMonday 16:00-21:00\n"
)


def test_text_locality_contradiction_ciceros_regression():
    # The document declares Interlochen (and the aggregator header Australia) but
    # never San Jose -> contradiction, reject before its items merge.
    assert L.text_locality_contradiction(
        CICEROS_INTERLOCHEN_PDF_TEXT, CICEROS_SJ_ADDR, "Cicero's Pizza") is True


def test_text_locality_handles_line_wrapped_country():
    # The real PDF wraps 'United\nStates' across lines -- must still read as a
    # country-terminated address.
    text = "Cicero's Pizza\n2408 M 137, Interlochen, United\nStates\n"
    assert L.text_locality_contradiction(text, CICEROS_SJ_ADDR, "Cicero's Pizza") is True


def test_text_locality_state_zip_shape_contradicts():
    text = "Joe's Diner\n12 Main St, Springfield, IL 62704\nPANCAKES $8"
    assert L.text_locality_contradiction(text, CICEROS_SJ_ADDR, "Joe's Diner") is True


def test_text_locality_home_city_mention_clears_it():
    # Corroboration first: the diner's city anywhere in the text -> never reject.
    text = "Cicero's Pizza\n6138 Bollinger Rd, San Jose, CA 95129\nCHEESE PIZZA $10"
    assert L.text_locality_contradiction(text, CICEROS_SJ_ADDR, "Cicero's Pizza") is False


def test_text_locality_no_declared_locality_is_not_a_contradiction():
    # A plain dish list declares nothing -> can't assert a mismatch.
    text = "Cicero's Pizza Menu\nCHEESE PIZZA $10\nPEPPERONI $12\nGARDEN SALAD $7"
    assert L.text_locality_contradiction(text, CICEROS_SJ_ADDR, "Cicero's Pizza") is False


def test_text_locality_name_country_tail_does_not_fire():
    # '<restaurant name>, USA' leaves no residual city token -> not a declaration.
    text = "Cicero's Pizza, USA\nBest pizza in town\nCHEESE PIZZA $10"
    assert L.text_locality_contradiction(text, CICEROS_SJ_ADDR, "Cicero's Pizza") is False


def test_text_locality_conservative_on_missing_inputs():
    assert L.text_locality_contradiction("", CICEROS_SJ_ADDR, "X") is False
    assert L.text_locality_contradiction(CICEROS_INTERLOCHEN_PDF_TEXT, "", "X") is False
    assert L.text_locality_contradiction(CICEROS_INTERLOCHEN_PDF_TEXT, None, "X") is False
