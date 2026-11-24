"""Write-once, content-addressed runs; no searches of historical MIDI/cache trees."""
import hashlib
import json
from pathlib import Path

SCHEMA = "canonical-midi-notes/1"
ANALYSIS_VERSION = "midi-sequences/1"
BACKEND = "yourmt3-plus"
REVISION = "5e66c1ea173a8186e0d20432b841d3180cc015b5"
EXPERIMENT = "mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops"
CHECKPOINT = f"amt/logs/2024/{EXPERIMENT}/checkpoints/last.ckpt"
CHECKPOINT_SHA = "ae38e415c79efd5592dcb9b658cdb99ddb11d4c4e1eaa364cab04a052473fc25"
DEFAULT_OUTPUT = Path("output/midi_pipeline/runs")
PIPELINE_CODE_FILES = ("__init__.py", "policy.py", "store.py", "schema.py", "backend.py",
                       "analysis.py", "diagnostics.py", "run.py", "separation.py")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def save(path, value):
    serialized = json.dumps(value, indent=2, allow_nan=False) + "\n"
    path = Path(path)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(path)


def implementation_digest():
    """Digest only production modules; changing tests/evaluation doesn't stale predictions."""
    module = Path(__file__).parent
    return fingerprint({name: digest(module / name) for name in PIPELINE_CODE_FILES})


def load_run(path):
    """Only this pipeline's completed, hash-checked prediction bundles are accepted."""
    path = Path(path)
    run = json.loads((path / "run.json").read_text(encoding="utf-8"))
    identity = run.get("identity", {})
    if (run.get("schema") != SCHEMA or run.get("status") != "prediction_available_unvalidated"
            or identity.get("backend") != BACKEND or identity.get("checkpoint_sha256") != CHECKPOINT_SHA
            or identity.get("analysis_version") != ANALYSIS_VERSION
            or identity.get("revision") != REVISION
            or identity.get("source_lock") != _source_lock_identity()
            or identity.get("code_sha256") != _implementation_digest()
            or run.get("run_id") != fingerprint(identity)):
        raise ValueError("Not a current, completed MIDI-first prediction bundle")
    if not all(name in run.get("artifacts", {}) for name in ("raw.mid", "notes.json")):
        raise ValueError("Missing native/canonical artifact hashes")
    for name in run["artifacts"]:
        if Path(name).name != name:
            raise ValueError("Invalid artifact path")
        if digest(path / name) != run.get("artifacts", {}).get(name):
            raise ValueError(f"Artifact hash mismatch: {name}")
    document = json.loads((path / "notes.json").read_text(encoding="utf-8"))
    if document.get("schema") != SCHEMA or document.get("run_id") != run["run_id"]:
        raise ValueError("Incompatible note document")
    from .schema import validate
    validate(document, identity)
    return run, document


def _source_lock_identity():
    lock = Path(__file__).with_name("yourmt3-source-lock.json")
    return hashlib.sha256(lock.read_bytes()).hexdigest() if lock.exists() else None


def _implementation_digest():
    return implementation_digest()
