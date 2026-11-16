//! Pure byte codecs. No Python, transport state, floating point or unsafe code.
//!
//! These are Rust copies of crc16, cobs_encode and cobs_decode in
//! sw/mathaccel/driver/framing.py. They must produce exactly the same bytes,
//! because the FPGA checks every frame with the same CRC and COBS rules.
use std::fmt;

// I build the CRC lookup table once, at compile time. Entry i is the CRC
// update for a high byte of i, so each input byte then costs one table
// lookup instead of eight shift-and-XOR steps.
const fn crc_table() -> [u16; 256] {
    let mut table = [0; 256];
    let mut i = 0;
    // A const fn can't use `for` loops, so I count with `while` instead.
    while i < 256 {
        let mut crc = (i as u16) << 8;
        let mut bit = 0;
        while bit < 8 {
            // Same feedback as the hardware's crc16_update: shift left, and
            // XOR in the polynomial 0x1021 when the top bit falls out.
            crc = if crc & 0x8000 != 0 { (crc << 1) ^ 0x1021 } else { crc << 1 };
            bit += 1;
        }
        table[i] = crc;
        i += 1;
    }
    table
}
const CRC: [u16; 256] = crc_table();

/// Update a CRC-16/CCITT with `data`. Pass 0xFFFF to start a new packet, or a
/// previous result to continue across several chunks.
pub fn crc16(data: &[u8], mut crc: u16) -> u16 {
    for &byte in data {
        // The CRC's high byte, mixed with the new byte, picks the table entry.
        // The low byte moves up to become the next high byte.
        crc = (crc << 8) ^ CRC[((crc >> 8) ^ u16::from(byte)) as usize];
    }
    crc
}

/// The three ways a received frame can be malformed. The messages match the
/// Python CobsError text so the logs read the same with either backend.
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

/// COBS-encode `data` so the result has no zero bytes. The transport adds the
/// final zero delimiter itself.
pub fn encode(data: &[u8]) -> Vec<u8> {
    // Worst case is one extra code byte per 254 data bytes, plus the first
    // one, so I reserve that much and the Vec never has to grow.
    let mut out = Vec::with_capacity(data.len() + data.len() / 254 + 1);
    // `marker` is where the current block's code byte goes. I don't know the
    // code until the block ends, so I push a placeholder 0 and fill it later.
    let mut marker = 0;
    let mut code = 1u8;
    out.push(0);
    for &byte in data {
        if byte == 0 {
            // A raw zero ends the block. Its code replaces the zero, so the
            // zero itself is never sent.
            out[marker] = code;
            marker = out.len();
            out.push(0);
            code = 1;
        } else {
            out.push(byte);
            code += 1;
            if code == 255 {
                // 254 data bytes is the most one block can hold. Code 255
                // means "full block, no zero after it", and a new block starts.
                out[marker] = code;
                marker = out.len();
                out.push(0);
                code = 1;
            }
        }
    }
    // Always close the last block, even if it ended up empty.
    out[marker] = code;
    out
}

/// Undo `encode` for one frame whose zero delimiter has already been removed.
pub fn decode(data: &[u8]) -> Result<Vec<u8>, DecodeError> {
    // The decoded frame is never longer than the encoded one.
    let mut out = Vec::with_capacity(data.len());
    let mut i = 0;
    while i < data.len() {
        let code = data[i];
        // A zero can't be a code byte: zeros only mark the end of a frame.
        if code == 0 { return Err(DecodeError::ZeroCode); }
        i += 1;
        let count = usize::from(code) - 1;
        // A code may not ask for more bytes than the frame actually has.
        // I compare against what's left so the check can't overflow.
        if count > data.len() - i { return Err(DecodeError::Overrun); }
        let block = &data[i..i + count];
        if block.contains(&0) { return Err(DecodeError::ZeroInBlock); }
        out.extend_from_slice(block);
        i += count;
        // A short block stood for a zero, but only between blocks. The last
        // block doesn't add a trailing zero.
        if code != 255 && i < data.len() { out.push(0); }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    // 0x29B1 is the published check value for CRC-16/CCITT-FALSE on
    // "123456789". Splitting the input in two must give the same answer.
    #[test]
    fn crc_known_and_chunks() {
        assert_eq!(crc16(b"123456789", 0xffff), 0x29b1);
        assert_eq!(crc16(b"56789", crc16(b"1234", 0xffff)), 0x29b1);
    }
    // Hand-checked encodings, plus round trips at the 254/255 block edges.
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
    // Each kind of bad frame must be caught, not decoded into garbage.
    #[test]
    fn malformed() {
        assert_eq!(decode(&[0]), Err(DecodeError::ZeroCode));
        assert_eq!(decode(&[5, 17]), Err(DecodeError::Overrun));
        assert_eq!(decode(&[2, 0]), Err(DecodeError::ZeroInBlock));
    }
}
