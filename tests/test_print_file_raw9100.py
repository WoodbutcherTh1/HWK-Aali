"""print_file raw-9100 route + text-only-driver guards (2026-09-22).

Pins the owner-printer lesson: 'Generic / Text Only' printers must get pure
ASCII bytes straight on TCP port 9100 (never GDI spooler garbage), must refuse
PDFs/images with a clear error, and must fall back to the legacy route when
the device is unreachable or the text is not ASCII.
"""

from __future__ import annotations

import socket
from unittest import mock

import pytest

from file_agent import file_tools


# ---------------------------------------------------------------- helpers

def test_tcp_host_from_port():
    assert file_tools._tcp_host_from_port("192.168.1.7_1") == "192.168.1.7"
    assert file_tools._tcp_host_from_port("192.168.1.7") == "192.168.1.7"
    assert file_tools._tcp_host_from_port("USB001") == ""
    assert file_tools._tcp_host_from_port("WSD-abc") == ""
    assert file_tools._tcp_host_from_port("") == ""


def test_is_text_only_driver():
    assert file_tools._is_text_only_driver("Generic / Text Only")
    assert file_tools._is_text_only_driver("generic text only")
    assert not file_tools._is_text_only_driver("HP LaserJet 200")
    assert not file_tools._is_text_only_driver("")


# ---------------------------------------------------------------- raw route

def _make_txt(tmp_path):
    target = tmp_path / "report.txt"
    target.write_text("hello printer\nline two\n", encoding="utf-8")
    return target


def test_ascii_text_on_tcp_port_goes_raw_9100(tmp_path):
    target = _make_txt(tmp_path)
    with mock.patch.object(file_tools, "_printer_port_and_driver",
                           return_value=("192.168.1.7_1", "Generic / Text Only")), \
         mock.patch.object(file_tools.socket, "create_connection") as conn:
        conn.return_value.__enter__.return_value = mock.MagicMock()
        result = file_tools.print_file(target.name, tmp_path, copies=1)
    assert result["ok"] is True
    assert result["route"] == "raw-9100"
    sent = conn.return_value.__enter__.return_value.sendall.call_args[0][0]
    assert sent.endswith(b"\x0c")  # form feed appended
    assert b"hello printer" in sent


def test_copies_repeats_payload(tmp_path):
    target = _make_txt(tmp_path)
    with mock.patch.object(file_tools, "_printer_port_and_driver",
                           return_value=("10.0.0.9_2", "Generic / Text Only")), \
         mock.patch.object(file_tools.socket, "create_connection") as conn:
        conn.return_value.__enter__.return_value = mock.MagicMock()
        file_tools.print_file(target.name, tmp_path, copies=3)
    sent = conn.return_value.__enter__.return_value.sendall.call_args[0][0]
    assert sent.count(b"hello printer") == 3


def test_non_ascii_text_falls_back_to_legacy(tmp_path):
    target = tmp_path / "ar.txt"
    target.write_text("مرحبا يا عالم\n", encoding="utf-8")
    with mock.patch.object(file_tools, "_printer_port_and_driver",
                           return_value=("192.168.1.7_1", "Generic / Text Only")), \
         mock.patch.object(file_tools, "_raw_print_9100", return_value=True) as raw, \
         mock.patch.object(file_tools.subprocess, "run",
                           return_value=mock.MagicMock(returncode=0)) as sp_run:
        result = file_tools.print_file(target.name, tmp_path, copies=1)
    raw.assert_not_called()  # non-ASCII never takes the raw route
    assert "route" not in result  # legacy path result has no raw marker
    sp_run.assert_called()  # legacy spooler path ran


def test_unreachable_device_falls_back(tmp_path):
    target = _make_txt(tmp_path)
    with mock.patch.object(file_tools, "_printer_port_and_driver",
                           return_value=("192.168.1.99_1", "Generic / Text Only")), \
         mock.patch.object(file_tools, "_raw_print_9100", return_value=False), \
         mock.patch.object(file_tools.subprocess, "run",
                           return_value=mock.MagicMock(returncode=0)) as sp_run:
        result = file_tools.print_file(target.name, tmp_path, copies=1)
    assert result["ok"] is True
    assert "route" not in result
    sp_run.assert_called()  # legacy 'print' command attempted


def test_text_on_non_tcp_port_uses_legacy(tmp_path):
    target = _make_txt(tmp_path)
    with mock.patch.object(file_tools, "_printer_port_and_driver",
                           return_value=("USB001", "Generic / Text Only")), \
         mock.patch.object(file_tools.socket, "create_connection") as conn, \
         mock.patch.object(file_tools.subprocess, "run",
                           return_value=mock.MagicMock(returncode=0)) as sp_run:
        result = file_tools.print_file(target.name, tmp_path, copies=1)
    conn.assert_not_called()
    sp_run.assert_called()
    assert "route" not in result


# ---------------------------------------------------------------- guards

def test_pdf_refused_on_text_only_driver(tmp_path):
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"%PDF-1.4 fake")
    with mock.patch.object(file_tools, "_printer_port_and_driver",
                           return_value=("192.168.1.7_1", "Generic / Text Only")):
        with pytest.raises(file_tools.FileAgentError, match="raw-text-only"):
            file_tools.print_file(target.name, tmp_path, copies=1)


def test_image_refused_on_text_only_driver(tmp_path):
    target = tmp_path / "pic.png"
    target.write_bytes(b"\x89PNG fake")
    with mock.patch.object(file_tools, "_printer_port_and_driver",
                           return_value=("192.168.1.7_1", "Generic / Text Only")):
        with pytest.raises(file_tools.FileAgentError, match="raw-text-only"):
            file_tools.print_file(target.name, tmp_path, copies=1)


# ---------------------------------------------------------------- raw helper

def test_raw_print_9100_sends_bytes():
    with mock.patch.object(file_tools.socket, "create_connection") as conn:
        conn.return_value.__enter__.return_value = mock.MagicMock()
        assert file_tools._raw_print_9100("192.168.1.7", b"x") is True
        conn.return_value.__enter__.return_value.sendall.assert_called_once()


def test_raw_print_9100_returns_false_on_socket_error():
    with mock.patch.object(file_tools.socket, "create_connection",
                           side_effect=OSError("no route")):
        assert file_tools._raw_print_9100("192.168.1.7", b"x") is False
