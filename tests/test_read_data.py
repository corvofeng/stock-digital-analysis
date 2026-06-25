from pathlib import Path

from stock_digital_analysis.read_data import resolve_input_file


def test_resolve_input_file_keeps_existing_dat_path(tmp_path):
    dat_file = tmp_path / "SH" / "60" / "601328.DAT"
    dat_file.parent.mkdir(parents=True)
    dat_file.write_bytes(b"")

    assert resolve_input_file(dat_file, tmp_path) == dat_file


def test_resolve_input_file_finds_symbol_under_data_dir(tmp_path):
    dat_file = tmp_path / "SH" / "60" / "601328.DAT"
    dat_file.parent.mkdir(parents=True)
    dat_file.write_bytes(b"")

    assert resolve_input_file(Path("601328.SH"), tmp_path) == dat_file
