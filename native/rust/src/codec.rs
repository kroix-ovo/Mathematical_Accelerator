//! Pure byte codecs. No Python, transport state, floating point or unsafe code.
use std::fmt;

const fn crc_table() -> [u16; 256] {
    let mut table = [0; 256];
    let mut i = 0;
    while i < 256 {
        let mut crc = (i as u16) << 8;
        let mut bit = 0;
        while bit < 8 {
            crc = if crc & 0x8000 != 0 { (crc << 1) ^ 0x1021 } else { crc << 1 };
            bit += 1;
        }
        table[i] = crc;
        i += 1;
    }
    table
}
const CRC: [u16; 256] = crc_table();

pub fn crc16(data: &[u8], mut crc: u16) -> u16 {
    for &byte in data {
        crc = (crc << 8) ^ CRC[((crc >> 8) ^ u16::from(byte)) as usize];
    }
    crc
}

#[derive(Debug, PartialEq, Eq)]
pub enum DecodeError { ZeroCode, Overrun, ZeroInBlock }

impl fmt::Display for DecodeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::ZeroCode => "zero byte inside COBS frame",
            Self::Overrun => "COBS block overruns frame",
            Self::ZeroInBlock => "zero byte inside COBS block",
        })
    }
}
impl std::error::Error for DecodeError {}

pub fn encode(data: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(data.len() + data.len() / 254 + 1);
    let mut marker = 0;
    let mut code = 1u8;
    out.push(0);
    for &byte in data {
        if byte == 0 {
            out[marker] = code;
            marker = out.len();
            out.push(0);
            code = 1;
        } else {
            out.push(byte);
            code += 1;
            if code == 255 {
                out[marker] = code;
                marker = out.len();
                out.push(0);
                code = 1;
            }
        }
    }
    out[marker] = code;
    out
}

pub fn decode(data: &[u8]) -> Result<Vec<u8>, DecodeError> {
    let mut out = Vec::with_capacity(data.len());
    let mut i = 0;
    while i < data.len() {
        let code = data[i];
        if code == 0 { return Err(DecodeError::ZeroCode); }
        i += 1;
        let count = usize::from(code) - 1;
        if count > data.len() - i { return Err(DecodeError::Overrun); }
        let block = &data[i..i + count];
        if block.contains(&0) { return Err(DecodeError::ZeroInBlock); }
        out.extend_from_slice(block);
        i += count;
        if code != 255 && i < data.len() { out.push(0); }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn crc_known_and_chunks() {
        assert_eq!(crc16(b"123456789", 0xffff), 0x29b1);
        assert_eq!(crc16(b"56789", crc16(b"1234", 0xffff)), 0x29b1);
    }
    #[test]
    fn exact_vectors() {
        assert_eq!(encode(&[]), [1]);
        assert_eq!(encode(&[0]), [1, 1]);
        assert_eq!(encode(&[17, 34, 0, 51]), [3, 17, 34, 2, 51]);
        for n in [1, 253, 254, 255, 508, 1024] {
            let data = vec![85; n];
            assert_eq!(decode(&encode(&data)), Ok(data));
            let zeros = vec![0; n];
            assert_eq!(encode(&zeros), vec![1; n + 1]);
        }
        assert_eq!(encode(&vec![85; 254]), [vec![255], vec![85; 254], vec![1]].concat());
    }
    #[test]
    fn malformed() {
        assert_eq!(decode(&[0]), Err(DecodeError::ZeroCode));
        assert_eq!(decode(&[5, 17]), Err(DecodeError::Overrun));
        assert_eq!(decode(&[2, 0]), Err(DecodeError::ZeroInBlock));
    }
}
