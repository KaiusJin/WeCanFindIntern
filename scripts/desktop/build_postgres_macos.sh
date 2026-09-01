#!/usr/bin/env bash
set -euo pipefail

postgres_version="${POSTGRES_VERSION:-16.15}"
vector_version="${PGVECTOR_VERSION:-0.8.1}"
openssl_version="${OPENSSL_VERSION:-3.5.8}"
openssl_sha256="${OPENSSL_SHA256:-a8f84a39918ec6415ce765d9b429d313ba97b8143169c172e734b9514464f5b2}"
deployment_target="${MACOSX_DEPLOYMENT_TARGET:-13.0}"
project_root="$(cd "$(dirname "$0")/../.." && pwd)"
python_bin="${PYTHON_BIN:-$project_root/.venv/bin/python}"
build_root="$(mktemp -d /private/tmp/wecanfindintern-postgres.XXXXXX)"
stage_prefix="$build_root/stage/opt/wecanfindintern/postgresql16"
openssl_prefix="$build_root/openssl"

export MACOSX_DEPLOYMENT_TARGET="$deployment_target"

cleanup() {
  if [[ "$build_root" == /private/tmp/wecanfindintern-postgres.* ]]; then
    rm -rf "$build_root"
  fi
}
trap cleanup EXIT

architecture="$(uname -m)"
if [[ "$architecture" == "arm64" ]]; then
  openssl_target="darwin64-arm64-cc"
  target="darwin-arm64"
else
  openssl_target="darwin64-x86_64-cc"
  target="darwin-x64"
fi

curl -fsSL \
  "https://github.com/openssl/openssl/releases/download/openssl-${openssl_version}/openssl-${openssl_version}.tar.gz" \
  -o "$build_root/openssl.tar.gz"
actual_openssl_sha256="$(shasum -a 256 "$build_root/openssl.tar.gz" | awk '{print $1}')"
if [[ "$actual_openssl_sha256" != "$openssl_sha256" ]]; then
  echo "OpenSSL checksum mismatch: expected $openssl_sha256, got $actual_openssl_sha256" >&2
  exit 1
fi
tar -xf "$build_root/openssl.tar.gz" -C "$build_root"

pushd "$build_root/openssl-${openssl_version}" >/dev/null
./Configure "$openssl_target" \
  no-apps \
  no-docs \
  no-shared \
  no-tests \
  --prefix="$openssl_prefix" \
  --openssldir="$openssl_prefix/ssl"
make -j"$(sysctl -n hw.logicalcpu)"
make install_sw
popd >/dev/null

openssl_archive="$(find "$openssl_prefix" -type f -name libcrypto.a -print -quit)"
if [[ -z "$openssl_archive" ]]; then
  echo "Static OpenSSL libcrypto.a was not produced" >&2
  exit 1
fi

curl -fsSL "https://ftp.postgresql.org/pub/source/v${postgres_version}/postgresql-${postgres_version}.tar.bz2" \
  -o "$build_root/postgresql.tar.bz2"
tar -xf "$build_root/postgresql.tar.bz2" -C "$build_root"

pushd "$build_root/postgresql-${postgres_version}" >/dev/null
./configure \
  --prefix=/opt/wecanfindintern/postgresql16 \
  --without-icu \
  --without-readline \
  --without-libxml \
  --without-libxslt \
  --without-openssl
make -j"$(sysctl -n hw.logicalcpu)"
make install "DESTDIR=$build_root/stage"

make -C contrib/pgcrypto clean
make -C contrib/pgcrypto \
  USE_PGXS=1 \
  "PG_CONFIG=$stage_prefix/bin/pg_config" \
  "CPPFLAGS=-I$openssl_prefix/include" \
  "SHLIB_LINK=-bundle_loader $stage_prefix/bin/postgres $openssl_archive -lz" \
  -j"$(sysctl -n hw.logicalcpu)"
make -C contrib/pgcrypto USE_PGXS=1 "PG_CONFIG=$stage_prefix/bin/pg_config" install

make -C contrib/pg_trgm \
  USE_PGXS=1 \
  "PG_CONFIG=$stage_prefix/bin/pg_config" \
  "SHLIB_LINK=-bundle_loader $stage_prefix/bin/postgres" \
  -j"$(sysctl -n hw.logicalcpu)"
make -C contrib/pg_trgm USE_PGXS=1 "PG_CONFIG=$stage_prefix/bin/pg_config" install
popd >/dev/null

git clone --depth 1 --branch "v${vector_version}" https://github.com/pgvector/pgvector.git "$build_root/pgvector"
make -C "$build_root/pgvector" "PG_CONFIG=$stage_prefix/bin/pg_config" OPTFLAGS="" -j"$(sysctl -n hw.logicalcpu)"
make -C "$build_root/pgvector" "PG_CONFIG=$stage_prefix/bin/pg_config" install

"$python_bin" "$project_root/scripts/desktop/prepare_postgres.py" --source "$stage_prefix" --target "$target"
"$python_bin" "$project_root/scripts/desktop/relocate_macos_postgres.py" \
  "$project_root/desktop/resources/postgres/$target" \
  --deployment-target "$deployment_target"
