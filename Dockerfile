# Build the Rust selector (no crates: std only).
FROM rust:1.91-slim-bookworm AS build
WORKDIR /b
COPY selector/Cargo.toml /b/Cargo.toml
COPY selector/src /b/src
RUN cargo build --release

# Runtime: the image the commits were labeled in.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock
ADD https://github.com/tobymao/sqlglot/archive/eb11aec4be8cb2122afe713a4816662c3f0fb2fa.tar.gz /tmp/sqlglot.tar.gz
RUN mkdir -p /app/src \
 && tar xzf /tmp/sqlglot.tar.gz -C /app/src --strip-components=1 \
 && rm /tmp/sqlglot.tar.gz \
 && python -m compileall -q /app/src/sqlglot /app/src/tests > /dev/null; \
    python -m compileall -q /usr/local/lib/python3.13 > /dev/null; true
COPY app/runner.py app/adapters.py app/map.txt.gz /app/
RUN gunzip /app/map.txt.gz && python -m compileall -q /app/runner.py /app/adapters.py > /dev/null; true
COPY --from=build /b/target/release/selector /app/selector
RUN chmod -R a+rX /app
ENV PYTHONDONTWRITEBYTECODE=1 SKIP_INTEGRATION=0
CMD ["/app/selector"]
