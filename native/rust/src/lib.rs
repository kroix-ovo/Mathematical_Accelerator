//! PyO3 boundary: borrow immutable input, return Python-owned immutable bytes.
//!
//! This file only connects Python to codec.rs. framing.py imports the built
//! module as mathaccel._framing_native and wraps these three functions.
mod codec;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

// PyO3 lends Rust a view of the Python bytes (`&[u8]`) without copying them.
// The CRC starts at 0xFFFF unless Python passes a running value.
#[pyfunction]
#[pyo3(signature = (data, crc=0xffff))]
fn crc16(data: &[u8], crc: u16) -> u16 { codec::crc16(data, crc) }

// I encode into a Rust Vec, then copy it once into a new Python bytes object.
#[pyfunction]
fn cobs_encode<'py>(py: Python<'py>, data: &[u8]) -> Bound<'py, PyBytes> {
    PyBytes::new(py, &codec::encode(data))
}

// A bad frame comes back to Python as ValueError with codec.rs's message.
// framing.py turns that into CobsError.
#[pyfunction]
fn cobs_decode<'py>(py: Python<'py>, data: &[u8]) -> PyResult<Bound<'py, PyBytes>> {
    let decoded = codec::decode(data).map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(PyBytes::new(py, &decoded))
}

// The module Python sees. `backend` lets framing.BACKEND report "rust", so
// every run records which code did the framing.
#[pymodule]
fn _framing_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("backend", "rust")?;
    m.add_function(wrap_pyfunction!(crc16, m)?)?;
    m.add_function(wrap_pyfunction!(cobs_encode, m)?)?;
    m.add_function(wrap_pyfunction!(cobs_decode, m)?)?;
    Ok(())
}
