# Source and originality note

The TKGS Navigator application code was rewritten for this project. The previous
project's Python files, its UI, its assets and its embedded access credentials were
not copied into this project. This is not development done without any reference.

The PID, table id and observed service/LCN field relationships used for TKGS were
understood by studying the behavior of Murat SEVER's `muratsevercom/enigma2-tkgs`
repository at revision `297ff971000d229466b48217da9b01f16f5aa6a1`. The reference
project's own rights and license notices apply to it.

Reference: https://github.com/muratsevercom/enigma2-tkgs/tree/297ff971000d229466b48217da9b01f16f5aa6a1

Linux DVB interface: https://docs.kernel.org/userspace-api/media/dvb/headers.html

lamedb 4/5 fields: https://github.com/OpenPLi/enigma2/blob/develop/lib/dvb/db.cpp

Enigma2 process and tuner APIs:
https://github.com/openatv/enigma2/blob/master/lib/python/Screens/Console.py
https://github.com/openatv/enigma2/blob/master/lib/python/Components/Sources/FrontendStatus.py

Example channel names and capture data are synthetic. No official affiliation with or
endorsement by Türksat or the Enigma2 image developers is claimed.
