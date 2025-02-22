#!/usr/bin/env python3
"""Take screen captures from DS1000Z-series oscilloscopes

This program captures either the waveform or the whole screen of a Rigol
DS1000Z series oscilloscope, then saves it on the computer as a CSV, PNG
or BMP file.

The program uses the LXI protocol, so the computer must have a LAN
connection with the oscilloscope.
"""

# Standard Library
import logging
import os
import platform
import subprocess
import sys
import time
import json
from pathlib import Path
import textwrap

# Library
from PIL import Image, ImageDraw, ImageFont
import arrow
import click

# Local
from Rigol_functions import (
    log_running_python_versions,
    command,
    tmc_header_bytes,
    expected_data_bytes,
    expected_buff_bytes,
)
from telnetlib_receive_all import Telnet

__version__ = 'v2.0.0u'
__author__ = 'RoGeorge'


CONFIG_FILENAME = 'config.json'
RIGOL_TELNET_PORT = 5555
TELNET_TIMEOUT_SECONDS = 1
INDEX_COMPANY = 0
INDEX_MODEL = 1


@click.command()
@click.argument('output_filename', required=False, default=None)
@click.option('-h', '--hostname', default=None, help='Oscilloscope IP address.')
@click.option('-n', '--note', help='Note label.')
@click.option('-1', '--label1', help='Channel 1 label.')
@click.option('-2', '--label2', help='Channel 2 label.')
@click.option('-3', '--label3', help='Channel 3 label.')
@click.option('-4', '--label4', help='Channel 4 label.')
@click.option(
    '-r',
    '--raw',
    'enable_raw',
    is_flag=True,
    help='Save raw image (with no annotation or de-cluttering).',
)
@click.option('-c', '--csv', 'save_as_csv', is_flag=True, help='Save scope data as csv.')
@click.option('-d', '--debug', 'enable_debug', is_flag=True, help='Enable debug logging.')
def main(
    hostname,
    output_filename,
    note,
    label1,
    label2,
    label3,
    label4,
    enable_raw,
    enable_debug,
    save_as_csv,
):
    """Take screen captures from DS1000Z-series oscilloscopes.

    \b
    OUTPUT_FILENAME: Name of output file to save.
        - If not supplied then a filename will be auto-generated using the current date/time.
        - If not supplied, and a note was supplied, and capturing a screenshot, then the
          filename will be auto-generated from the note.  If the target filename exists, then
          then the suffix "_n" (with increasing values of n) will be appended.

    Passing the --csv flag will save the capture samples as a CSV file.
    If the --csv flag is NOT passed, then a screenshot (.png) will be saved.
    """

    # -----------------------------
    # Initialize Logging
    # -----------------------------
    # Users may call this app from a different directory, so we will figure out the
    # absolute path to the log file in this module's directory.
    module_path = Path(__file__).parent
    log_path = module_path / Path(os.path.basename(sys.argv[0]) + '.log')
    logging.basicConfig(
        level=logging.DEBUG if enable_debug else logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        filename=log_path,
        filemode='w',
    )
    logging.info("***** New run started...")
    logging.info("OS Platform: " + str(platform.uname()))
    log_running_python_versions()

    # -----------------------------
    # Wrangle command line arguments
    # -----------------------------
    extension_validation = (
        (True, '.csv', 'csv data'),
        (False, '.png', 'screenshot'),
    )
    for expect_save_as_csv, expect_suffix, capture_description in extension_validation:
        if (
            save_as_csv == expect_save_as_csv
            and output_filename
            and Path(output_filename).suffix != expect_suffix
        ):
            print(
                f'ERROR: Output filename "{output_filename}" should have {expect_suffix}'
                f' suffix when capturing {capture_description}'
            )
            sys.exit()

    with open(module_path / Path(CONFIG_FILENAME), 'r') as file:
        config = json.load(file)
    if hostname is None:
        hostname = config['default_hostname']

    output_dir_path = None
    if output_filename:
        output_dir_path, output_filename = extract_parent(output_filename)
    if output_dir_path is None:
        output_dir_path = (
            Path.cwd()
            if config['default_save_path'] == '$cwd'
            else Path(config['default_save_path'])
        )
    logging.debug(f'Preliminary output target: {output_dir_path=}, {output_filename=}')

    # -----------------------------
    # Connect to scope
    # -----------------------------
    if not test_ping(hostname):
        sys.exit()

    # Open a modified telnet session
    # The default telnetlib drops 0x00 characters,
    #   so a modified library 'telnetlib_receive_all' is used instead
    telnet = Telnet(hostname, RIGOL_TELNET_PORT)
    instrument_id = command(telnet, '*IDN?').decode()  # ask for instrument ID

    # Check if instrument is set to accept LAN commands
    if instrument_id == "command error":
        print(f'Instrument reply: {instrument_id}')
        print('Check the oscilloscope settings.')
        print('Utility -> IO Setting -> RemoteIO -> LAN must be ON.')
        sys.exit('ERROR')

    # Check if instrument is indeed a Rigol DS1000Z series
    id_fields = instrument_id.split(",")
    if (
        (id_fields[INDEX_COMPANY] != "RIGOL TECHNOLOGIES")
        or (id_fields[INDEX_MODEL][:3] != "DS1")
        or (id_fields[INDEX_MODEL][-1] != "Z")
    ):
        print(
            f'Found instrument model "{id_fields[INDEX_MODEL]}" from "{id_fields[INDEX_COMPANY]}"'
        )
        print(f'WARNING: No Rigol from series DS1000Z found at {hostname}\n')
        typed = raw_input('ARE YOU SURE YOU WANT TO CONTINUE? (No/Yes):')
        if typed != 'Yes':
            sys.exit('Nothing done. Bye!')

    print(f'Instrument ID: "{instrument_id.strip()}".')

    # -----------------------------
    # Determine output filename
    # -----------------------------
    timestamp_time = time.localtime()
    if output_filename is None:
        suffix = 'csv' if save_as_csv else 'png'
        output_filename = build_save_filename(timestamp_time, id_fields[INDEX_MODEL], suffix, note)
    output_path = output_dir_path / Path(output_filename)
    logging.debug(f'Final output target: {output_path}')

    # -----------------------------
    # Capture
    # -----------------------------
    if save_as_csv:
        capture_csv_data(output_path, telnet)
    else:
        capture_screenshot(output_path, telnet)
        if not enable_raw:
            annotate(output_path, timestamp_time, note, label1, label2, label3, label4)
    telnet.close()


def capture_screenshot(output_path, telnet):
    # Ask for an oscilloscope display print screen
    print("Receiving screen capture...")
    buff = command(telnet, ":DISP:DATA? ON,OFF,PNG")
    expected_buffer_length = expected_buff_bytes(buff)
    # Just in case the transfer did not complete in the expected time, read the remaining 'buff' chunks
    while len(buff) < expected_buffer_length:
        logging.warning(
            "Received LESS data then expected! ("
            + str(len(buff))
            + " out of "
            + str(expected_buffer_length)
            + " expected 'buff' bytes.)"
        )
        tmp = telnet.read_until(b"\n", TELNET_TIMEOUT_SECONDS)
        if len(tmp) == 0:
            break
        buff += tmp
        logging.warning(str(len(tmp)) + " leftover bytes added to 'buff'.")

    if len(buff) < expected_buffer_length:
        logging.error(
            "After reading all data chunks, 'buff' is still shorter then expected! ("
            + str(len(buff))
            + " out of "
            + str(expected_buffer_length)
            + " expected 'buff' bytes.)"
        )
        sys.exit("ERROR")

    # Strip TMC Blockheader and keep only the data
    tmcHeaderLen = tmc_header_bytes(buff)
    expectedDataLen = expected_data_bytes(buff)
    buff = buff[tmcHeaderLen : tmcHeaderLen + expectedDataLen]

    # Write raw data to file
    with open(output_path, 'wb') as f:
        f.write(buff)
    print(f'Saved raw image to "{humanize_path(output_path)}".')


def capture_csv_data(output_path, telnet):
    # Put the scope in STOP mode - for the moment, deal with it by manually stopping the scope
    # TODO: Add command line switch and code logic for 1200 vs ALL memory data points
    # TODO: Change WAV:FORM from ASC to BYTE
    # tn.write("stop")
    # response = tn.read_until("\n", 1)

    # Scan for displayed channels
    chanList = []
    for channel in ["CHAN1", "CHAN2", "CHAN3", "CHAN4", "MATH"]:
        response = command(telnet, ":" + channel + ":DISP?")

        # If channel is active
        if response == b'1\n':
            chanList += [channel]

    # the meaning of 'max' is   - will read only the displayed data when the scope is in RUN mode,
    #                             or when the MATH channel is selected
    #                           - will read all the acquired data points when the scope is in STOP mode
    # TODO: Change mode to MAX
    # TODO: Add command line switch for MAX/NORM
    command(telnet, ":WAV:MODE NORM")
    command(telnet, ":WAV:STAR 0")
    command(telnet, ":WAV:MODE NORM")

    csv_buff = ""

    # for each active channel
    for channel in chanList:
        print()

        # Set WAVE parameters
        command(telnet, ":WAV:SOUR " + channel)
        command(telnet, ":WAV:FORM ASC")

        # MATH channel does not allow START and STOP to be set. They are always 0 and 1200
        if channel != "MATH":
            command(telnet, ":WAV:STAR 1")
            command(telnet, ":WAV:STOP 1200")

        buff = b''
        print(
            "Data from channel '"
            + str(channel)
            + "', points "
            + str(1)
            + "-"
            + str(1200)
            + ": Receiving..."
        )
        buffChunk = command(telnet, ":WAV:DATA?")

        # Just in case the transfer did not complete in the expected time
        while buffChunk[-1] != "\n":
            logging.warning(
                "The data transfer did not complete in the expected time of "
                + str(TELNET_TIMEOUT_SECONDS)
                + " second(s)."
            )

            tmp = telnet.read_until(b"\n", TELNET_TIMEOUT_SECONDS)
            if len(tmp) == 0:
                break
            buffChunk += tmp
            logging.warning(str(len(tmp)) + " leftover bytes added to 'buff_chunks'.")

        # Append data chunks
        # Strip TMC Blockheader and terminator bytes
        buff += buffChunk[tmc_header_bytes(buffChunk) : -1] + b","

        # Strip the last \n char
        buff = buff[:-1]

        # Process data
        buff_list = buff.split(b",")

        # Put read data into csv_buff
        csv_buff_list = csv_buff.split(os.linesep)
        csv_rows = len(csv_buff_list)

        current_row = 0
        if csv_buff == "":
            csv_first_column = True
            csv_buff = str(channel) + os.linesep
        else:
            csv_first_column = False
            csv_buff = str(csv_buff_list[current_row]) + "," + str(channel) + os.linesep

        for point in buff_list:
            point_str = point.decode("utf-8")
            current_row += 1
            if csv_first_column:
                csv_buff += point_str + os.linesep
            else:
                if current_row < csv_rows:
                    csv_buff += str(csv_buff_list[current_row]) + "," + point_str + os.linesep
                else:
                    csv_buff += "," + point_str + os.linesep

    # Save data as CSV
    scr_file = open(output_path, "w")
    scr_file.write(csv_buff)
    scr_file.close()

    print(f'Saved file: "{humanize_path(output_path)}".')


def build_save_filename(timestamp_time, scope_model, suffix, note):
    # Preapeare filename as: MODEL_SERIAL_YYYY-MM-DD_HH.MM.SS
    timestamp = time.strftime("%Y-%m-%d_%H.%M.%S", timestamp_time)
    filename = f"{scope_model}_{timestamp}.{suffix}"
    if note is None:
        return filename

    # Build filename from note name
    filename_base = note.replace(' ', '_')
    for i in range(100):
        file_number = '' if i == 0 else f'_{i+1}'
        filename_candidate = f'{filename_base}{file_number}.{suffix}'
        path = Path(filename_candidate)
        if not path.exists():
            return filename_candidate
    return filename


def annotate(output_path, timestamp_time, note, label1, label2, label3, label4):
    """Annotate and declutter image.

    - The following image "clutter" is automatically removed:
        - Left on-screen menu.
        - Right on-screen menu.
        - Upper left RIGOL logo.
        - Lower right status icons (sound, etc.)
    - The following annotation is automatically added:
        - Time/Date stamp (Upper left)
    - The following annotations are optionally added:
        - Note ("-n" option)
        - Signal labels (options "-1", "-2", "-3", "-4")
    """
    print("Annotating image...")
    image = Image.open(output_path)
    draw = ImageDraw.Draw(image)
    # Users may call this app from a different directory, so need to figure out the
    # absolute path to the font file in this module's directory.
    font_path = Path(__file__).parent / Path('Inconsolata-SemiBold.ttf')
    font = ImageFont.truetype(str(font_path), 12)

    # Erase logo
    draw.rectangle(((3, 8), (80, 28)), fill=0)
    # Erase left menu and enclosing box
    draw.rectangle(((0, 37), (59, 450)), fill=0)
    # Erase right menu items
    draw.rectangle(((705, 38), (799, 436)), fill=0)
    # Erase right menu "tab" text (menu title)
    draw.rectangle(((690, 39), (704, 117)), fill=0)
    # Erase lower right speaker on/off icon
    draw.rectangle(((762, 456), (799, 479)), fill=0)

    # Draw timestamp
    arrow_time = arrow.get(timestamp_time)
    time_text = str(arrow_time)
    time_text = time_text[0:10] + '\n' + time_text[11:19]
    draw.text((4, 2), time_text, font=font, fill=(255, 255, 255))

    # Draw channel labels
    image = image.rotate(90, expand=True)  # Counterclockwise
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), 16)
    location = [40, 1]
    labels = (
        (note, "#b0b0b0"),
        (label1, "#F7FA52"),
        (label2, "#00E1DD"),
        (label3, "#DD00DD"),
        (label4, "#007FF5"),
    )
    for index, item in enumerate(labels):
        label_text, color = item
        if label_text:
            text = f'CH{index}: {label_text}' if index > 0 else f'{label_text}'
            draw.text(location, text, font=font, fill=color)
            location[1] += 18  # Line spacing
    image = image.rotate(-90, expand=True)  # Clockwise

    image.save(output_path)
    print("Done.")


def extract_parent(filename):
    """Extract the parent path from a filename, if one EXPLICITLY exists.

    Examples:
        "foo.png"             ==> (None "foo.png")
        "./foo.png"           ==> (Path("./"), "foo.png")
        "/Users/John/foo.png" ==> (Path("/Users/John/"), "foo.png")

    Args:
        filename: A string conaining a filename with or without path information.

    Returns:
        A tuple of fhe form (parent, filename).
    """
    parent = Path(filename).parent
    filename_raw = Path(filename).name
    if filename == filename_raw:
        # No explicit path (e.g. "./" was given)
        return None, filename
    # An explicit path (e.g. "./" was given)
    return parent, filename_raw


def humanize_path(path):
    """Return path (as string) relative to the current working dir.

    The goal is to provide a "human readable" path for display to a user.

    - If the path is IN or BELOW the current working directory then return a RELATIVE path string.
    - If the path is ABOVE the current working directory then return an ABSOLUTE path string.
    """
    relative_path = path.relative_to(Path.cwd())
    if str(relative_path).startswith('..'):
        return str(path)
    return str(relative_path)


def test_ping(hostname):
    """Ping hostname to see if it responds.

    Returns:
        True if host responded, False otherwise
    """
    if platform.system() == "Windows":
        command = ['ping', '-n', '1', hostname]
    else:
        command = ['ping', '-c', '1', hostname]
    result = subprocess.run(command, capture_output=True)
    stdout_text = result.stdout.decode('utf-8')
    
    if result.returncode != 0:
        print(
            '\n'
            f'ERROR: No response pinging "{hostname}".\n'
            'Check network cables and settings.\n'
            'You should be able to ping the oscilloscope.'
        )
        logging.error(f'Ping of {hostname} failed.')
        return False
    elif "Destination host unreachable" in stdout_text:
        print(
            '\n'
            f'ERROR: Ping to "{hostname}" failed.\n'
            f'Ping result:\n{textwrap.indent(stdout_text, "   ")}\n'
            'Check network cables and settings.\n'
            'You should be able to ping the oscilloscope.'
        )
        logging.error(f'Ping of {hostname} failed.')
        return False

    logging.info(f'Ping of {hostname} succeeded.')
    logging.debug(f'{stdout_text=}')
    logging.debug(f'{result.stderr=}')
    return True


if __name__ == "__main__":
    main()
