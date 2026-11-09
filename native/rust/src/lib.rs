//! PyO3 boundary: borrow immutable input, return Python-owned immutable bytes.
mod codec;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

#[pyfunction]
#[pyo3(signature = (data, crc=0xffff))]
fn crc16(data: &[u8], crc: u16) -> u16 { codec::crc16(data, crc) }

#[pyfunction]
fn cobs_encode<'py>(py: Python<'py>, data: &[u8]) -> Bound<'py, PyBytes> {
    PyBytes::new(py, &codec::encode(data))
}

#[pyfunction]
fn cobs_decode<'py>(py: Python<'py>, data: &[u8]) -> PyResult<Bound<'py, PyBytes>> {
    let decoded = codec::decode(data).map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(PyBytes::new(py, &decoded))
}

#[pymodule]
fn _framing_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("backend", "rust")?;
    m.add_function(wrap_pyfunction!(crc16, m)?)?;
    m.add_function(wrap_pyfunction!(cobs_encode, m)?)?;
    m.add_function(wrap_pyfunction!(cobs_decode, m)?)?;
    Ok(())
}
