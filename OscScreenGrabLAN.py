#!/usr/bin/env python3
"""Take screen captures from DS1000Z-series oscilloscopes

This program captures either the waveform or the whole screen of a Rigol
DS1000Z series oscilloscope, then saves it on the computer as a CSV, PNG
or BMP file.

The program uses the LXI protocol, so the computer must have a LAN
connection with the oscilloscope.
"""

from enum import Enum, auto
import argparse
import logging
import os
import platform
import subprocess
import sys
import time
from PIL import Image, ImageDraw, ImageFont
import arrow
import pathlib

from Rigol_functions import *
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

# Set the desired logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
# EPM: Users may call this app from a different directory, so need to figure out the
#      absolute path to the log file in this module's directory.
log_path = pathlib.Path(__file__).parent / pathlib.Path(os.path.basename(sys.argv[0]) + '.log')
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    filename=log_path,
                    filemode='w')

logging.info("***** New run started...")
logging.info("OS Platform: " + str(platform.uname()))
log_running_python_versions()

# Update the next lines for your own default settings:
# path_to_save = "captures/"
path_to_save = os.getcwd() + '/'
IP_DS1104Z_DEFAULT_IP = "169.254.247.73"

# Rigol/LXI specific constants
port = 5555

big_wait = 10
smallWait = 1

company = 0
model = 1
serial = 2


# Read/verify file type
class FileType(Enum):
    png = auto()
    bmp = auto()
    csv = auto()


# Check network response (ping)
def test_ping(hostname):
    """Ping hostname once"""
    if platform.system() == "Windows":
        command = ['ping', '-n', '1', hostname]
    else:
        command = ['ping', '-c', '1', hostname]
    completed = subprocess.run(command, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)

    if completed.returncode != 0:
        print()
        print("WARNING! No response pinging", hostname)
        print("Check network cables and settings.")
        print("You should be able to ping the oscilloscope.")

def run(hostname, filename, filetype, args):
    test_ping(hostname)

    # Open a modified telnet session
    # The default telnetlib drops 0x00 characters,
    #   so a modified library 'telnetlib_receive_all' is used instead
    tn = Telnet(hostname, port)
    instrument_id = command(tn, "*IDN?").decode()    # ask for instrument ID

    # Check if instrument is set to accept LAN commands
    if instrument_id == "command error":
        print ("Instrument reply:", instrument_id)
        print ("Check the oscilloscope settings.")
        print ("Utility -> IO Setting -> RemoteIO -> LAN must be ON")
        sys.exit("ERROR")

    # Check if instrument is indeed a Rigol DS1000Z series
    id_fields = instrument_id.split(",")
    if (id_fields[company] != "RIGOL TECHNOLOGIES") or \
            (id_fields[model][:3] != "DS1") or (id_fields[model][-1] != "Z"):
        print ("Found instrument model '{}' from '{}'".format(id_fields[model], id_fields[company]))
        print ("WARNING: No Rigol from series DS1000Z found at", hostname)
        print ()
        typed = raw_input("ARE YOU SURE YOU WANT TO CONTINUE? (No/Yes):")
        if typed != 'Yes':
            sys.exit('Nothing done. Bye!')

    print ("Instrument ID:", instrument_id)

    # Prepare filename as C:\MODEL_SERIAL_YYYY-MM-DD_HH.MM.SS
    timestamp_time = time.localtime()
    timestamp = time.strftime("%Y-%m-%d_%H.%M.%S", timestamp_time)
    if filename is None:
        filename = f"{path_to_save}{id_fields[model]}_{timestamp}.{filetype.name}"
        if args.note is not None:
            filename_base = args.note.replace(' ', '_')
            suffix = ''
            for i in range(20):
                suffix = '' if i == 0 else f'_{i+1}'
                filename_candidate = f'{filename_base}{suffix}.{filetype.name}'
                path = pathlib.Path(filename_candidate)
                if not path.exists():
                    filename = filename_candidate
                    break

    if filetype in {FileType.png, FileType.bmp}:
        # Ask for an oscilloscope display print screen
        print ("Receiving screen capture...")

        if filetype is FileType.png:
            buff = command(tn, ":DISP:DATA? ON,OFF,PNG")
        else:
            buff = command(tn, ":DISP:DATA? ON,OFF,BMP8")

        expectedBuffLen = expected_buff_bytes(buff)
        # Just in case the transfer did not complete in the expected time, read the remaining 'buff' chunks
        while len(buff) < expectedBuffLen:
            logging.warning("Received LESS data then expected! (" +
                            str(len(buff)) + " out of " + str(expectedBuffLen) + " expected 'buff' bytes.)")
            tmp = tn.read_until(b"\n", smallWait)
            if len(tmp) == 0:
                break
            buff += tmp
            logging.warning(str(len(tmp)) + " leftover bytes added to 'buff'.")

        if len(buff) < expectedBuffLen:
            logging.error("After reading all data chunks, 'buff' is still shorter then expected! (" +
                          str(len(buff)) + " out of " + str(expectedBuffLen) + " expected 'buff' bytes.)")
            sys.exit("ERROR")

        # Strip TMC Blockheader and keep only the data
        tmcHeaderLen = tmc_header_bytes(buff)
        expectedDataLen = expected_data_bytes(buff)
        buff = buff[tmcHeaderLen: tmcHeaderLen+expectedDataLen]

        # Write raw data to file
        with open(filename, 'wb') as f:
            f.write(buff)
        print('Saved raw data to "{}"'.format(filename))

        # -------------------------------
        # Replace Rigol logo with timestamp
        # -------------------------------
        print("Stripping logo...")
        image = Image.open(filename)
        draw = ImageDraw.Draw(image)
        # Users may call this app from a different directory, so need to figure out the
        # absolute path to the font file in this module's directory.
        font_path = pathlib.Path(__file__).parent / pathlib.Path('Inconsolata-SemiBold.ttf')
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
        image = image.rotate(90, expand=True) # Counterclockwise
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(font_path), 16)
        location = [40, 1]
        labels = (
            (args.note, "#b0b0b0"),
            (args.label1, "#F7FA52"),
            (args.label2, "#00E1DD"),
            (args.label3, "#DD00DD"),
            (args.label4, "#007FF5"),
        )
        for index, item in enumerate(labels):
            label_text, color = item
            if label_text:
                text = f'CH{index}: {label_text}' if index > 0 else f'{label_text}'
                draw.text(location, text, font=font, fill=color)
                location[1] += 18 # Line spacing
        image = image.rotate(-90, expand=True) # Clockwise

        image.save(filename)
        print("Done.")


    # TODO: Change WAV:FORM from ASC to BYTE
    elif filetype is FileType.csv:
        # Put the scope in STOP mode - for the moment, deal with it by manually stopping the scope
        # TODO: Add command line switch and code logic for 1200 vs ALL memory data points
        # tn.write("stop")
        # response = tn.read_until("\n", 1)

        # Scan for displayed channels
        chanList = []
        for channel in ["CHAN1", "CHAN2", "CHAN3", "CHAN4", "MATH"]:
            response = command(tn, ":" + channel + ":DISP?")

            # If channel is active
            if response == '1\n':
                chanList += [channel]

        # the meaning of 'max' is   - will read only the displayed data when the scope is in RUN mode,
        #                             or when the MATH channel is selected
        #                           - will read all the acquired data points when the scope is in STOP mode
        # TODO: Change mode to MAX
        # TODO: Add command line switch for MAX/NORM
        command(tn, ":WAV:MODE NORM")
        command(tn, ":WAV:STAR 0")
        command(tn, ":WAV:MODE NORM")

        csv_buff = ""

        # for each active channel
        for channel in chanList:
            print ()

            # Set WAVE parameters
            command(tn, ":WAV:SOUR " + channel)
            command(tn, ":WAV:FORM ASC")

            # MATH channel does not allow START and STOP to be set. They are always 0 and 1200
            if channel != "MATH":
                command(tn, ":WAV:STAR 1")
                command(tn, ":WAV:STOP 1200")

            buff = ""
            print ("Data from channel '" + str(channel) + "', points " + str(1) + "-" + str(1200) + ": Receiving...")
            buffChunk = command(tn, ":WAV:DATA?")

            # Just in case the transfer did not complete in the expected time
            while buffChunk[-1] != "\n":
                logging.warning("The data transfer did not complete in the expected time of " +
                                str(smallWait) + " second(s).")

                tmp = tn.read_until(b"\n", smallWait)
                if len(tmp) == 0:
                    break
                buffChunk += tmp
                logging.warning(str(len(tmp)) + " leftover bytes added to 'buff_chunks'.")

            # Append data chunks
            # Strip TMC Blockheader and terminator bytes
            buff += buffChunk[tmc_header_bytes(buffChunk):-1] + ","

            # Strip the last \n char
            buff = buff[:-1]

            # Process data
            buff_list = buff.split(",")
            buff_rows = len(buff_list)

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

        print ("Saved file:", "'" + filename + "'")

    tn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Take screen captures from"
            " DS1000Z-series oscilloscopes")
    parser.add_argument("-t", "--type",
            choices=FileType.__members__,
            help="Optional type of file to save")
    parser.add_argument("hostname", nargs="?",
            help="Hostname or IP address of the oscilloscope")
    parser.add_argument("filename", nargs="?",
            help="Optional name of output file")
    parser.add_argument("-1", "--label1",
            help="Channel 1 label")
    parser.add_argument("-2", "--label2",
            help="Channel 2 label")
    parser.add_argument("-3", "--label3",
            help="Channel 3 label")
    parser.add_argument("-4", "--label4",
            help="Channel 4 label")
    parser.add_argument("-n", "--note",
            help="Note label")

    args = parser.parse_args()

    # # If no type is specified, auto-detect from the filename
    # if args.type is None:
    #     if args.filename is None:
    #         parser.error("Either a file type or a filename must be specified")
    #     args.type = os.path.splitext(args.filename)[1][1:]

    # EPM: Just default to png
    if args.type is None:
        args.type = 'png'
    # EPM: default host
    if args.hostname is None:
        args.hostname = IP_DS1104Z_DEFAULT_IP

    try:
        args.type = FileType[args.type]
    except KeyError:
        parser.error("Unknown file type: {}".format(args.type))

    run(args.hostname, args.filename, args.type, args)