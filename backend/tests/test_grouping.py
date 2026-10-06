"""Source-independent grouping-v6 contracts: taxonomy, normalization, models, mining, relists."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.grouping.assign import ModelDictionary, ModelEntry, classify_type
from app.services.grouping.mining import MiningSample, mine_phrases
from app.services.grouping.normalize import TitleNormalizer
from app.services.grouping.policy import REVIEW_TYPE, GroupingPolicy, load_policy
from app.services.grouping.relists import RelistRow, detect_relists
from app.services.grouping.text import words

NOW = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def policy() -> GroupingPolicy:
    return load_policy()


def _normalizer(policy: GroupingPolicy, brand: str, slug: str, *aliases: str) -> TitleNormalizer:
    return TitleNormalizer(policy, brand_terms=[brand, *aliases], brand_slug=slug)


def _dictionary(
    policy: GroupingPolicy, normalizer: TitleNormalizer, slug: str
) -> ModelDictionary:
    entries: list[ModelEntry] = []
    names: dict[tuple[str, str], int] = {}
    seeds = policy.seed_models(slug)
    for seed in seeds:
        for product_type in seed.types or ("tshirt", "hoodie", "longsleeve"):
            names[(product_type, seed.name)] = len(names) + 1
    for seed in seeds:
        for product_type in seed.types or ("tshirt", "hoodie", "longsleeve"):
            phrases = tuple(
                tokens for value in seed.aliases if (tokens := normalizer.phrase(value))
            )
            entries.append(
                ModelEntry(
                    key=names[(product_type, seed.name)],
                    product_type=product_type,
                    name=seed.name,
                    phrases=phrases,
                    parent=names.get((product_type, seed.parent)) if seed.parent else None,
                    infer=seed.infer == product_type,
                )
            )
    return ModelDictionary(
        entries,
        protected_modifiers=policy.words.protected_modifiers,
        version_numbers=policy.words.version_numbers,
    )


def test_every_configured_category_path_resolves_to_a_real_type(policy: GroupingPolicy) -> None:
    taxonomy = policy.taxonomy
    assert len(taxonomy.categories) >= 130
    for path, rule in taxonomy.categories.items():
        resolved, known = taxonomy.rule_for(path)
        assert known and resolved is rule
        for type_id in (*rule.any, rule.fallback, rule.fixed):
            if type_id is not None:
                assert type_id in taxonomy.types and type_id != REVIEW_TYPE, path
    for type_id, product_type in taxonomy.types.items():
        assert product_type.section in taxonomy.sections, type_id
    # Unknown paths fall back to their department instead of failing.
    rule, known = taxonomy.rule_for("outerwear.some_new_category")
    assert not known and rule.fallback == "outerwear_other"
    assert taxonomy.rule_for(None)[0].fallback == "other"


def test_every_seed_dictionary_is_valid(policy: GroupingPolicy) -> None:
    assert {"chrome-hearts", "rick-owens", "balenciaga"} <= set(policy.seeds)
    for slug, models in policy.seeds.items():
        names = {model.name for model in models}
        for model in models:
            assert model.parent is None or model.parent in names, (slug, model.name)


def test_title_words_fold_accents_plurals_and_versions() -> None:
    assert [item.norm for item in words("Déprimés Geobaskets Track.2 Track 2.0 sz 9.5")] == [
        "deprime",
        "geobasket",
        "track",
        "2",
        "track",
        "2",
        "sz",
        "9.5",
    ]


def test_normalization_removes_brand_typos_type_color_size_and_noise(
    policy: GroupingPolicy,
) -> None:
    rick = _normalizer(policy, "Rick Owens", "rick-owens", "RO", "DRKSHDW")
    title = rick.normalize("RARE Rick Ownes Geobaskets Milk size 45 sneakers BNWT 🔥 FW19")
    assert title.tokens == ("geobasket",)
    assert title.strong_types == ("lowtop_sneakers",)
    chrome = _normalizer(policy, "Chrome Hearts", "chrome-hearts", "CH")
    assert chrome.normalize("Cheome Hearts Dagger Pendant 925").tokens == ("dagger",)
    assert chrome.normalize("Chrome Hearts Dagger Trucker Hat Black OS").tokens == ("dagger",)
    assert rick.normalize("Rick Owens Geo Basket EU 42").tokens == ("geobasket",)


def test_dagger_hat_and_dagger_pendant_never_share_a_group(policy: GroupingPolicy) -> None:
    normalizer = _normalizer(policy, "Chrome Hearts", "chrome-hearts", "CH")
    dictionary = _dictionary(policy, normalizer, "chrome-hearts")
    taxonomy = policy.taxonomy
    hat = normalizer.normalize("Chrome Hearts Dagger Trucker Hat")
    pendant = normalizer.normalize("Chrome Hearts Dagger Pendant")
    bare = normalizer.normalize("Chrome Hearts Dagger")
    hat_type = classify_type(taxonomy, "accessories.hats", hat, dictionary)
    pendant_type = classify_type(taxonomy, "accessories.jewelry_watches", pendant, dictionary)
    inferred = classify_type(taxonomy, "accessories.jewelry_watches", bare, dictionary)
    assert (hat_type.product_type, hat_type.method) == ("hat", "category")
    assert (pendant_type.product_type, pendant_type.method) == ("pendant", "title")
    assert (inferred.product_type, inferred.method) == ("pendant", "model")
    assert dictionary.match("hat", hat.tokens) is None  # no seed Dagger hat: "No model"
    pendant_match = dictionary.match("pendant", pendant.tokens)
    assert pendant_match is not None and pendant_match.entry.name == "Dagger"
    # A pendant listed under hats goes to review, never into a hat group.
    miscategorized = classify_type(taxonomy, "accessories.hats", pendant, dictionary)
    assert miscategorized.product_type == REVIEW_TYPE
    # A ring with no type words or known model stays untyped jewelry.
    unknown = normalizer.normalize("Chrome Hearts Mystery Piece")
    assert classify_type(taxonomy, "accessories.jewelry_watches", unknown, dictionary) \
        .product_type == "jewelry_other"


def test_erd_tee_without_the_word_tee_gets_its_type_from_the_category(
    policy: GroupingPolicy,
) -> None:
    normalizer = _normalizer(policy, "Enfants Riches Déprimés", "enfants-riches-deprimes", "ERD")
    dictionary = _dictionary(policy, normalizer, "enfants-riches-deprimes")
    title = normalizer.normalize("ERD Crimes De L'Amour")
    decision = classify_type(policy.taxonomy, "tops.short_sleeve_shirts", title, dictionary)
    assert decision.product_type == "tshirt"
    match = dictionary.match("tshirt", title.tokens)
    assert match is not None and match.entry.name == "Crimes de l'Amour"


def test_track_versions_belong_to_the_track_line(policy: GroupingPolicy) -> None:
    normalizer = _normalizer(policy, "Balenciaga", "balenciaga")
    dictionary = _dictionary(policy, normalizer, "balenciaga")
    line = dictionary.match("lowtop_sneakers", normalizer.normalize("Balenciaga Track").tokens)
    second = dictionary.match(
        "lowtop_sneakers", normalizer.normalize("Balenciaga Track.2.0 Sneakers").tokens
    )
    third = dictionary.match("lowtop_sneakers", normalizer.normalize("Balenciaga Track 3").tokens)
    sized = dictionary.match("lowtop_sneakers", normalizer.normalize("Balenciaga Track 42").tokens)
    assert line is not None and line.entry.name == "Track" and line.version is None
    assert second is not None and second.entry.name == "Track 2"
    assert second.entry.parent == line.entry.key
    assert third is not None and third.entry.name == "Track" and third.version == ("track", "3")
    assert sized is not None and sized.version is None  # sizes are not versions


def test_protected_modifier_and_typo_match_inside_a_line(policy: GroupingPolicy) -> None:
    normalizer = _normalizer(policy, "Rick Owens", "rick-owens", "RO")
    dictionary = _dictionary(policy, normalizer, "rick-owens")
    mega = dictionary.match(
        "hitop_sneakers", normalizer.normalize("Rick Owens Mega Bumper Geobasket").tokens
    )
    typo = dictionary.match("hitop_sneakers", normalizer.normalize("Rick Owens Geobaskt").tokens)
    assert mega is not None and mega.entry.name == "Geobasket"
    assert mega.version == ("mega", "geobasket")
    assert typo is not None and typo.entry.name == "Geobasket" and typo.fuzzy


def test_mining_requires_distinct_sellers_and_prefers_longer_phrases() -> None:
    def sample(seller: str, *tokens: str) -> MiningSample:
        return MiningSample(seller, tokens, tuple(token.title() for token in tokens))

    spam = [sample("one-seller", "devil", "disguise") for _ in range(12)]
    real = [sample(f"seller-{index}", "crimes", "de", "lamour") for index in range(5)]
    noise = [sample(f"other-{index}", "random", f"word{index}") for index in range(4)]
    mined = mine_phrases([*spam, *real, *noise])
    assert [item.tokens for item in mined] == [("crimes", "de", "lamour")]
    assert mined[0].sellers == 5 and mined[0].name == "Crimes De Lamour"
    assert mine_phrases(real, excluded=[("crimes", "de", "lamour")]) == []


def test_relists_follow_one_seller_and_stop_at_a_sale() -> None:
    def row(
        listing_id: int, seller: str, status: str, created_days: int, seen_days: int
    ) -> RelistRow:
        return RelistRow(
            id=listing_id,
            grailed_id=listing_id + 1000,
            seller=seller,
            group_id=7,
            status=status,
            created_at=NOW - timedelta(days=created_days),
            last_seen_at=NOW - timedelta(days=seen_days),
        )

    rows = [
        row(1, "a", "removed", 100, 60),
        row(2, "a", "removed", 59, 30),
        row(3, "a", "sold", 29, 10),
        row(4, "a", "active", 5, 0),
        row(5, "b", "active", 50, 0),
        row(6, "a", "removed", 300, 250),
        row(7, "a", "active", 120, 0),
    ]
    roots = detect_relists(rows)
    assert roots[2] == 1 and roots[3] == 1
    assert 4 not in roots  # the item sold; a new listing is a different item
    assert 5 not in roots  # another seller
    assert 7 not in roots  # more than 90 days after the old listing ended
    reposted = RelistRow(
        id=9, grailed_id=2009, seller=None, group_id=7, status="active",
        created_at=NOW, last_seen_at=NOW, repost_of=1006,
    )
    assert detect_relists([*rows, reposted])[9] == 6
