"""Lists > Rules shows how many rows each rule labels (walk item P7.2): the
rule list carries `rows_labelled`, counted against the rule that wins for each
row the rules labelled. Rows labelled by hand, transfers and card payments are
not counted. Every name and figure here is invented."""

from test_book_and_type_switch import merchant, row, rule, statement  # noqa: F401


def shown_rule(client, rule_id):
    return next(x for x in client.get("/api/rules").get_json() if x["id"] == rule_id)


def test_each_rule_counts_the_rows_it_labels(client, conn, statement):  # noqa: F811
    ads = merchant(conn, "Sample Ads", "Moom", "Advertising")
    busy = rule(conn, "SMPL ADS", ads)
    idle = rule(conn, "SMPL NEVER SEEN", ads)
    longer = rule(conn, "SMPL ADS EXTRA", ads)   # longer pattern wins its rows
    row(conn, statement, "SMPL ADS 0042", cat_source="service_default")
    row(conn, statement, "SMPL ADS 0043")   # labelled at import: "auto"
    row(conn, statement, "SMPL ADS EXTRA 0044", cat_source="service_default")
    row(conn, statement, "SMPL ADS 0099", cat_source="manual")
    row(conn, statement, "SMPL ADS 0100", cat_source="service_default", flow="transfer")

    assert shown_rule(client, busy)["rows_labelled"] == 2
    assert shown_rule(client, longer)["rows_labelled"] == 1
    assert shown_rule(client, idle)["rows_labelled"] == 0
