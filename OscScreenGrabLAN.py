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
from enum import Enum, auto

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
# Added TMC Blockheader decoding
# Added possibility to manually allow run for scopes other then DS1000Z
__author__ = 'RoGeorge'

#
# TODO: Write all SCPI commands in their short name, with capitals
# TODO: Add ignore instrument model switch instead of asking
#
# TODO: Detect if the scope is in RUN or in STOP mode (looking at the length of data extracted)
# TODO: Add logic for 1200/mdep points to avoid displaying the 'Invalid Input!' message
# TODO: Add message for csv data points: mdep (all) or 1200 (screen), depending on RUN/STOP state, MATH and WAV:MODE
# TODO: Add STOP scope switch
#
# TODO: Add debug switch
# TODO: Clarify info, warning, error, debug and print messages
#
# TODO: Add automated version increase
#
# TODO: Extract all memory datapoints. For the moment, CSV is limited to the displayed 1200 datapoints.
# TODO: Use arrays instead of strings and lists for csv mode.
#
# TODO: variables/functions name refactoring
# TODO: Fine tune maximum chunk size request
# TODO: Investigate scaling. Sometimes 3.0e-008 instead of expected 3.0e-000
# TODO: Add timestamp and mark the trigger point as t0
# TODO: Use channels label instead of chan1, chan2, chan3, chan4, math
# TODO: Add command line parameters file path
# TODO: Speed-up the transfer, try to replace Telnet with direct TCP
# TODO: Add GUI
# TODO: Add browse and custom filename selection
# TODO: Create executable distributions
#

CONFIG_FILENAME = 'config.json'
RIGOL_TELNET_PORT = 5555
TELNET_TIMEOUT_SECONDS = 1
INDEX_COMPANY = 0
INDEX_MODEL = 1


@click.command()
@click.argument('hostname', required=False, default=None)
@click.argument('filename', required=False)
@click.option('-t', '--type', 'file_extension', default='png', help='Type of file to save.')
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
    help='Save raw image (with no annotation or de-cluttering)',
)
@click.option('-d', '--debug', 'enable_debug', is_flag=True, help='Enable debug logging.')
@click.option('-c', '--csv', 'save_as_csv', is_flag=True, help='Save scope data as csv.')
def main(
    hostname,
    filename,
    file_extension,
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
    hostname: Hostname or IP address of the oscilloscope.  If not supplied (or the word
              "default") then the value of "default_hostname" from config.json will be used.
    filename: Name of output file.

    """

    # -----------------------------
    # Initialize Logging
    # -----------------------------
    # Users may call this app from a different directory, so we will figure out the
    # absolute path to the log file in this module's directory.
    log_path = Path(__file__).parent / Path(os.path.basename(sys.argv[0]) + '.log')
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
    with open(CONFIG_FILENAME, 'r') as file:
        config = json.load(file)
    if hostname in (None, 'default'):
        hostname = config['default_hostname']

    save_path = config['default_save_path']
    if save_path == '$cwd':
        save_path = os.getcwd() + '/'

    # -----------------------------
    # Begin
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

    timestamp_time = time.localtime()

    if filename is None:
        suffix = 'csv' if save_as_csv else 'png'
        filename = build_save_filename(
            save_path, timestamp_time, id_fields[INDEX_MODEL], suffix, note
        )

    if save_as_csv:
        capture_csv_data(filename, telnet)
    else:
        capture_screenshot(filename, telnet)
        if not enable_raw:
            annotate(filename, timestamp_time, note, label1, label2, label3, label4)
    telnet.close()


def capture_screenshot(filename, telnet):

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
    with open(filename, 'wb') as f:
        f.write(buff)
    print(f'Saved raw image to "{filename}".')


def capture_csv_data(filename, telnet):
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
        if response == '1\n':
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

        buff = ""
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
        buff += buffChunk[tmc_header_bytes(buffChunk) : -1] + ","

        # Strip the last \n char
        buff = buff[:-1]

        # Process data
        buff_list = buff.split(",")

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
            current_row += 1
            if csv_first_column:
                csv_buff += str(point) + os.linesep
            else:
                if current_row < csv_rows:
                    csv_buff += str(csv_buff_list[current_row]) + "," + str(point) + os.linesep
                else:
                    csv_buff += "," + str(point) + os.linesep

    # Save data as CSV
    scr_file = open(filename, "wb")
    scr_file.write(csv_buff)
    scr_file.close()

    print(f'Saved file: "{filename}".')


def build_save_filename(save_path, timestamp_time, scope_model, suffix, note):
    # Preapeare filename as: MODEL_SERIAL_YYYY-MM-DD_HH.MM.SS
    timestamp = time.strftime("%Y-%m-%d_%H.%M.%S", timestamp_time)
    filename = f"{save_path}{scope_model}_{timestamp}.{suffix}"
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


def annotate(filename, timestamp_time, note, label1, label2, label3, label4):
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

    # -------------------------------
    # Replace Rigol logo with timestamp
    # -------------------------------
    print("Annotating image...")
    image = Image.open(filename)
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

    image.save(filename)
    print("Done.")


# Check network response (ping)
def test_ping(hostname):
    """Ping hostname to see if it responds.

    Returns:
        True if host responded, False otherwise
    """
    if platform.system() == "Windows":
        command = ['ping', '-n', '1', hostname]
    else:
        command = ['ping', '-c', '1', hostname]
    completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if completed.returncode != 0:
        print(
            '\n'
            f'WARNING! No response pinging "{hostname}"".\n'
            'Check network cables and settings.\n'
            'You should be able to ping the oscilloscope.'
        )
        return False
    return True


if __name__ == "__main__":
    main()
