"""HTTP-transport-only middleware. Tool-call middleware (logging, error
normalization) lives in server.py via FastMCP's own Middleware class instead
— this package is specifically for Starlette ASGI middleware that wraps the
HTTP transport, which is where request-header-based auth belongs."""
