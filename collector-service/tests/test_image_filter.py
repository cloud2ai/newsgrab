from app.image_filter import filter_images

PAGE_URL = "https://example.com/news/article-1"


def test_keeps_absolute_http_image_urls():
    images = ["https://cdn.example.com/photos/a.jpg", "https://cdn.example.com/photos/b.png"]
    assert filter_images(images, PAGE_URL) == images


def test_resolves_relative_urls_against_page_url():
    result = filter_images(["/media/photo.jpg"], PAGE_URL)
    assert result == ["https://example.com/media/photo.jpg"]


def test_drops_non_http_schemes():
    images = [
        "data:image/png;base64,iVBORw0KGgo=",
        "javascript:alert(1)",
        "ftp://example.com/a.jpg",
    ]
    assert filter_images(images, PAGE_URL) == []


def test_drops_svg_and_icon_extensions():
    images = ["https://example.com/logo.svg", "https://example.com/favicon.ico"]
    assert filter_images(images, PAGE_URL) == []


def test_drops_urls_matching_non_content_keywords():
    images = [
        "https://example.com/img/logo.png",
        "https://example.com/img/icon-share.png",
        "https://example.com/img/avatar-42.jpg",
        "https://example.com/img/sprite.png",
        "https://example.com/img/pixel.gif",
        "https://example.com/img/1x1.gif",
        "https://example.com/img/spacer.gif",
        "https://example.com/img/placeholder.jpg",
        "https://example.com/img/badge-verified.png",
    ]
    assert filter_images(images, PAGE_URL) == []


def test_keeps_real_content_photo_alongside_dropped_junk():
    images = [
        "https://example.com/img/logo.png",
        "https://cdn.example.com/uploads/2026/07/rally-photo.jpg",
    ]
    assert filter_images(images, PAGE_URL) == ["https://cdn.example.com/uploads/2026/07/rally-photo.jpg"]


def test_dedupes_preserving_first_occurrence_order():
    images = [
        "https://cdn.example.com/a.jpg",
        "https://cdn.example.com/b.jpg",
        "https://cdn.example.com/a.jpg",
    ]
    assert filter_images(images, PAGE_URL) == [
        "https://cdn.example.com/a.jpg",
        "https://cdn.example.com/b.jpg",
    ]


def test_caps_result_at_max_images():
    images = [f"https://cdn.example.com/{i}.jpg" for i in range(30)]
    result = filter_images(images, PAGE_URL, max_images=5)
    assert result == images[:5]


def test_ignores_query_string_when_checking_extension_and_keywords():
    result = filter_images(["https://cdn.example.com/photo.jpg?logo=1&size=large"], PAGE_URL)
    assert result == ["https://cdn.example.com/photo.jpg?logo=1&size=large"]


def test_empty_input_returns_empty_list():
    assert filter_images([], PAGE_URL) == []


def test_drops_malformed_urls_without_raising():
    assert filter_images(["not a url", ""], PAGE_URL) == []
