use sha2::{Digest, Sha256};
fn main() {
    for (bytes, expected) in [
        (Vec::new(), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
        (b"abc".to_vec(), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
        (vec![b'a';1_000_000], "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0")
    ] {
        let mut hash=Sha256::new();
        for chunk in bytes.chunks(65536) { hash.update(chunk); }
        let actual=hash.finalize().iter().map(|b|format!("{b:02x}")).collect::<String>();
        assert_eq!(actual,expected);
    }
    println!("Forced software SHA-256 backend: 3 streaming known-answer cases passed");
}
