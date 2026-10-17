import hashlib
from pathlib import Path
import subprocess

from tools.github_test_session import decrypt_token, generate_key, matches_request


def test_ephemeral_key_exchange_roundtrip(tmp_path):
    private = tmp_path / "private.pem"
    public = tmp_path / "public.pem"
    public.write_bytes(generate_key(private))
    payload = b"synthetic-test-token"
    encrypted = subprocess.run([
        "openssl", "pkeyutl", "-encrypt", "-pubin", "-inkey", str(public),
        "-pkeyopt", "rsa_padding_mode:oaep", "-pkeyopt", "rsa_oaep_md:sha256", "-pkeyopt", "rsa_mgf1_md:sha256",
    ], input=payload, capture_output=True, check=True).stdout
    assert payload not in encrypted
    assert decrypt_token(encrypted, private).encode() == payload
    assert private.stat().st_mode & 0o777 == 0o600


def test_encrypted_token_is_bound_to_the_expected_runner_key():
    sha = hashlib.sha256(b"synthetic-public-key").hexdigest()
    metadata = {"run_id": "123", "public_key_sha256": sha}
    assert matches_request(metadata, "123", sha)
    assert not matches_request(metadata, "124", sha)
    assert not matches_request(metadata, "123", "another-key")
    assert not matches_request(None, "123", sha)
