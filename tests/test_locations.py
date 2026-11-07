def test_locations_page(env):  # brief item 2: every location classified
    html = env.client.get("/locations").text
    assert html.count('class="verdict') == 38
    assert "Confident match" in html and "Medium match, nothing to change" in html


def test_locations_filter(env):
    html = env.client.get("/locations?verdict=Confident match").text
    assert html.count('class="verdict') == 9
