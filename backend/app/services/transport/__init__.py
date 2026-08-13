"""HTTP transport, resilience and proxy primitives."""

from app.services.transport.factory import create_http_transport, create_proxy_manager
from app.services.transport.mock_http import MOCK_ALGOLIA_BASE_URL, MockHttpTransport
from app.services.transport.protocols import HttpResponse, HttpTransport

__all__ = [
    "HttpResponse",
    "HttpTransport",
    "MOCK_ALGOLIA_BASE_URL",
    "MockHttpTransport",
    "create_http_transport",
    "create_proxy_manager",
]
