# region ----- Import packages -----
import srs_sr830
import srs_srcs580
import adwin
import lakeshore_tc336
import oxford_mercury_itc
import pyvisa
import os
from numpy import mean, std, log10, min, max, nanmin, nanmax, ctypeslib, ma
import pickle
from Objects.Backup.measurement_objects import *
from Objects.Backup.plot_objects import *
from Utilities.signal_processing import *
import time
import datetime

# endregion

#######################################################################
#   Author:         Davide Beretta
#   Date:           26.07.2021
#   Description:    TE-FET: TE stability diagram (for Stanford and ADwin Gold II)
#
#   Instrumentation settings are remotely set and controlled only if
#   the user provides the interface address.
#
#   ADwin Analog Output - Analog Input configuration
#   AO1: DC gate
#   AO2: DC bias
#   AI1: AC current at 2omega_1 x-component
#   AI2: AC current at 2omega_1 y-component
#   AI3: AC current at omega_2 x-component
#   AI4: AC current at omega_2 y-component
#   AI5: DC (raw) current
#   AI6: DC voltage across the DUT (when measuring low impedance DUTs, otherwise not connected)
#   AI7: DC voltage at omega2_1 x-component (when measuring low impedance DUTs, otherwise not connected)
#   AI8: DC voltage at omega2_1 y-component (when measuring low impedance DUTs, otherwise not connected)
#######################################################################

# region ----- Measurement options -----
experiment = Experiment()
experiment.experiment = "te stability diagram"
experiment.main = r"E:\Samples\dabe"
experiment.date = datetime.datetime.now()
experiment.chip = "teps02"
experiment.device = "d2"
experiment.filename = f"{experiment.chip} - {experiment.device} - {experiment.experiment}.data"
experiment.backupname = f"{experiment.filename}.bak"
# --------------------------------------------------------------------------------------------------------------------------------------------------------------
heater = 1                             # [int] select heater in use (it has no practical effect on the code)
mode = 1                               # [int] select method. 0: highly resistive samples (2 lock-in), 1: low resistive samples (3 lock-in)
t = array([293])                       # [1D array] temperatures (in K).
i_h = array([3e-3])        # [1D array] heater current (in A).
vb = linspace(-5e-3, 5e-3, 1)          # [1D array] array of dc bias voltage (graphene: -5 mV to +5 mV)
vg = linspace(-100, 100, 101)             # [1D array] array of gate voltage
v_ex = 100e-6                            # [float] amplitude of the AC excitation (in V)
v_ex_divider = 1/100 * 1/10                      # [float] The voltage divider (e.g. 1/100)
vb_divider = 1/10
vg_steps = 100                         # [int] number of steps when increasing / decreasing the DC gate voltage
vb_steps = 100                         # [int] number of steps when increasing / decreasing the DC bias voltage
heater_settling_time = 60 * 3        # [float] settling time (in s) after settings heater current
# endregion ----------------------------------------------------------------------------------------------------------------------------------------------------

# region ----- Settings ------
settings = EmptyClass()
# ----- tc settings -----
settings.__setattr__("tc", EmptyClass())
settings.tc.model = 1                       # [int] select tc model (0: lakeshore 336, 1: oxford mercury itc)
settings.tc.address = "ASRL8::INSTR"        # [string] address of temperature controller
settings.tc.t_switch = 50                   # [float] temperature (in K) below which the Lakeshore 336 heater range is set to "medium"
settings.tc.sampling_freq = 1               # [int] temperature sampling frequency (in Hz)
settings.tc.settling_time_init = 0 * 60    # [float] cryostat thermalization time (in s).
settings.tc.settling_time = settings.tc.settling_time_init  # [float] cryostat thermalization time (in s).
# ----- adc settings -----
settings.__setattr__("adc", EmptyClass())
settings.adc.model = 0                      # [int] select adc model (0: adwin gold ii)
settings.adc.input_resolution = 18          # [int] input resolution (in bit)
settings.adc.output_resolution = 16         # [int] output resolution (in bit)
settings.adc.clock_freq = 300e6             # [int] clock frequency (in Hz)
settings.adc.line_freq = 50                 # [int] line frequency (in Hz)
settings.adc.scanrate = 50e3               # [int] frequency at which the script is executed (in Hz)
settings.adc.nplc = 1                       # [float] number of power line cycles
settings.adc.iv_settling_time = 0.1          # [float] measurement time (in s). The number of samples is: vt_time / (nplc / line_freq)
settings.adc.vt_settling_time = 60 * 3          # [float] measurement time (in s). The number of samples is: vt_time / (nplc / line_freq)
settings.adc.vt_measurement_time = 60 * 1       # [float] measurement time (in s). The number of samples is: vt_time / (nplc / line_freq)
# ----- src3 settings -----
settings.__setattr__("src3", EmptyClass())
settings.src3.model = 0                     # [int] select src3 model (0: srs cs580, 1: tu delft XXX)
settings.src3.address = "ASRL11::INSTR"     # [string] address. Set None if not connected to PC
settings.src3.gain = 10e-3                    # [float] gain (in A/V)
settings.src3.isolation = "float"           # [string] isolation
settings.src3.shield = "return"             # [string] shield
settings.src3.compliance = 50               # [float] compliance (in V)
settings.src3.response = "slow"             # [string] response
settings.src3.delay = 0.1                   # [string] current source settling time (in s)
settings.src3.rate = 10e-6                  # [float] current sweep rate (in A/s). Used in current sweep.
settings.src3.step = 1e-6                   # [float] current step (in A). Used in current sweep.
# ----- avv1 (gate) settings -----
settings.__setattr__("avv1", EmptyClass())
settings.avv1.model = 2                     # [int] select amplifier model (0: srs sr560, 1: tu delft XXX, 2: basel)
settings.avv1.address = None                # [string] address. Set None if not connected to PC
settings.avv1.gain = 10                    # [float] gain (in V/V)
settings.avv1.lpf = None                    # [float] low pass filter cutoff frequency (in Hz)
settings.avv1.hpf = None                    # [float] low pass filter cutoff frequency (in Hz)
settings.avv1.coupling = "dc"               # [string] coupling (ac or dc)
# ----- avi settings -----
settings.__setattr__("avi", EmptyClass())
settings.avi.model = 0                     # [int] select amplifier model (0: Femto ddpca-300)
settings.avi.address = None                # [string] address. Set None if not connected to PC
settings.avi.gain = 1e6                    # [float] gain (in V/A)
settings.avi.lpf = 400                     # [float] low pass filter cutoff frequency (in Hz)
settings.avi.hpf = None                    # [float] low pass filter cutoff frequency (in Hz)
settings.avi.coupling = "dc"               # [string] coupling (ac or dc)
# ----- div (voltage divider) -----
# settings.div.model = 0                      # [int] select divider model (0: Basel)
# settings.div.gain = 1/100                   # [float] gain (in V/V)
# ----- lock-in1 settings -----
# When using external adc, Output = (signal/sensitivity - offset) x Expand x 10 V
# Set offset = 0 and Expand = 1 (default)
settings.__setattr__("lockin1", EmptyClass())
settings.lockin1.address = "GPIB0::1::INSTR"  # [string] address of lockin 1
settings.lockin1.reference = "internal"
settings.lockin1.freq = 3.745               # [float] excitation current frequency (in Hz)
settings.lockin1.shield = "float"           # [string] shield ca nbe floating or shorted
settings.lockin1.coupling = "ac"            # [string] coupling can be ac or dc
settings.lockin1.time = 3                   # [float] integration time
settings.lockin1.filter = "24 dB/oct"       # [string] filter
settings.lockin1.input = "a"              # [string] input can be single ended or differential
settings.lockin1.sensitivity = 50e-3            # [string] sensitivity (in V). If input signal is current, sensitivity (in A) = sensitivity (in V) * 1e-6 A/V
settings.lockin1.harmonic = 2               # [int] demodulated harmonic
settings.lockin1.reserve = "normal"         # [string] reserve
# ----- lock-in2 settings -----
settings.__setattr__("lockin2", EmptyClass())
settings.lockin2.address = "GPIB0::2::INSTR"  # [string] address of lockin 2
settings.lockin2.reference = "internal"
settings.lockin2.freq = 5.685               # [float] excitation current frequency (in Hz)
settings.lockin2.shield = "float"           # [string] shield ca nbe floating or shorted
settings.lockin2.coupling = "ac"            # [string] coupling can be ac or dc
settings.lockin2.time = 3                   # [float] integration time
settings.lockin2.filter = "24 dB/oct"       # [string] filter
settings.lockin2.input = "a"              # [string] input can be single ended or differential
settings.lockin2.sensitivity = 50e-3            # [string] sensitivity (in V). If input signal is current, sensitivity (in A) = sensitivity (in V) * 1e-6 A/V
settings.lockin2.harmonic = 1               # [int] demodulated harmonic
settings.lockin2.reserve = "normal"         # [string] reserve
if mode == 1:
    # ----- lock-in3 settings -----
    settings.__setattr__("lockin3", EmptyClass())
    settings.lockin3.address = "GPIB0::3::INSTR"            # [string] address of lockin 2
    settings.lockin3.reference = "external"
    settings.lockin3.freq = settings.lockin1.freq  # [float] excitation current frequency (in Hz)
    settings.lockin3.shield = "float"           # [string] shield ca nbe floating or shorted
    settings.lockin3.coupling = "ac"            # [string] coupling can be ac or dc
    settings.lockin3.time = 3                   # [float] integration time
    settings.lockin3.filter = "24 dB/oct"       # [string] filter
    settings.lockin3.input = "a"              # [string] input can be single ended or differential
    settings.lockin3.sensitivity = 50e-3        # [string] sensitivity (in V). If input signal is current, sensitivity (in A) = sensitivity (in V) * 1e-6 A/V
    settings.lockin3.harmonic = 1               # [int] demodulated harmonic
    settings.lockin3.reserve = "normal"         # [string] reserve
    # ----- avv2 (DC voltage across DUT) settings -----
    settings.__setattr__("avv2", EmptyClass())
    settings.avv2.model = 1                     # [int] select amplifier model (0: srs sr560, 1: tu delft XXX, 2: basel)
    settings.avv2.address = None                # [string] address. Set None if not connected to PC
    settings.avv2.gain = 1e3                    # [float] gain (in V/V)
    settings.avv2.lpf = 200                     # [float] low pass filter cutoff frequency (in Hz)
    settings.avv2.hpf = None                    # [float] low pass filter cutoff frequency (in Hz)
    settings.avv2.coupling = "dc"               # [string] coupling (ac or dc)
# ----------
experiment.settings = settings
# endregion

# region ----- Calculate ETA -----
eta = len(t) * (settings.tc.settling_time +
                len(i_h) * len(vb) * len(vg) * (settings.adc.vt_settling_time + settings.adc.vt_measurement_time))
# endregion

# region ----- Message to the user -----
print(f"""\n
***** Measurement summary *****
Chip: {experiment.chip}
Device: {experiment.device}
Heater: {heater}
Temperatures: {"".join([f"{x:.1f} K, " for x in t])}
Heater current: {"".join([f"{1e3*x:.1f} mA, " for x in i_h])}
Gate voltage: from {min(vg)} V to {max(vg)} V in {len(vg)} steps
Bias voltage: from {min(vb)} V to {max(vb)} V in {len(vb)} steps
Estimated measurement time: {eta/3600:.1f} h.""")
input("Press Enter to accept and proceed, press Ctrl-C to abort.")
# endregion

# region ----- Load drivers -----
print("\n***** Loading instrumentation drivers and configuring *****")
rm = pyvisa.ResourceManager()

if settings.adc.model == 0:
    boot_dir = "C:/ADwin/ADwin11.btl"  # directory of boot file
    routines_dir = "C:/Python scripts/lab-scripts/Instrumentation library/Adwin/Gold II"  # directory of routines files
    adc = adwin.adwin(boot_dir, routines_dir)
    adc.adw.Load_Process(routines_dir + "/sweep_2_ao_read_2_ai.TB1")
    adc.adw.Set_Processdelay(1, int(ceil(settings.adc.clock_freq / settings.adc.scanrate)))
    adc.adw.Load_Process(routines_dir + "/read_8_ai_gold_ii.TB2")
    adc.adw.Set_Processdelay(2, int(ceil(settings.adc.clock_freq / settings.adc.scanrate)))
    adc.adw.Load_Process(routines_dir + "/sweep_2_ao.TB4")
    adc.adw.Set_Processdelay(4, int(ceil(settings.adc.clock_freq / settings.adc.scanrate)))
    # n. of points to average in hardware = nplc / line freq * scanrate
    adc.adw.Set_Par(33, int(ceil(settings.adc.nplc / settings.adc.line_freq * settings.adc.scanrate)))
    # in hardware settling time: no. of loops to wait after setting output)
    adc.adw.Set_Par(34, int(ceil(settings.adc.iv_settling_time * settings.adc.scanrate)))
    # number of samples of a time-dependent measurement = time / nplc * line_freq
    adc.adw.Set_Par(71, int(ceil((settings.adc.vt_settling_time + settings.adc.vt_measurement_time) / (settings.adc.nplc / settings.adc.line_freq))))
    print(f"ADC-DAC: ADwin Gold II drivers loaded and configured.")

if settings.tc.address is not None and settings.tc.model == 0:
    tc = lakeshore_tc336.tc336(visa=rm.open_resource(settings.tc.address))
if settings.tc.address is not None and settings.tc.model == 1:
    tc = oxford_mercury_itc.mercuryitc(visa=rm.open_resource(settings.tc.address))
    print(f"Temperature controller: {tc.read_model()} drivers loaded.")

if settings.src3.address is not None and settings.src3.model == 0:
    src3 = srs_srcs580.srcs580(visa=rm.open_resource(settings.src3.address))
    src3.configure(settings.src3.gain, settings.src3.response, settings.src3.shield,
                   settings.src3.isolation, "off", "on", settings.src3.compliance)
    print(f"Current source 1: {src3.read_model()} drivers loaded and configured.")

if settings.lockin1.address is not None:
    lockin1 = srs_sr830.sr830(visa=rm.open_resource(settings.lockin1.address))
    lockin1.configure(settings.lockin1.reference, 0, settings.lockin1.freq, settings.lockin1.harmonic,
                      settings.lockin1.input, settings.lockin1.shield, settings.lockin1.coupling,
                      settings.lockin1.sensitivity, settings.lockin1.reserve, settings.lockin1.time,
                      settings.lockin1.filter, "both")
    print(f"Lockin 1: {lockin1.read_model()} drivers loaded and configured.")

if settings.lockin2.address is not None:
    lockin2 = srs_sr830.sr830(visa=rm.open_resource(settings.lockin2.address))
    lockin2.configure(settings.lockin2.reference, 0, settings.lockin2.freq, settings.lockin2.harmonic,
                      settings.lockin2.input, settings.lockin2.shield, settings.lockin2.coupling,
                      settings.lockin2.sensitivity, settings.lockin2.reserve, settings.lockin2.time,
                      settings.lockin2.filter, "both")
    print(f"Lockin 2: {lockin2.read_model()} drivers loaded and configured.")

if mode == 1 and settings.lockin3.address is not None:
    lockin3 = srs_sr830.sr830(visa=rm.open_resource(settings.lockin3.address))
    lockin3.configure(settings.lockin3.reference, 0, settings.lockin3.freq, settings.lockin3.harmonic,
                      settings.lockin3.input, settings.lockin3.shield, settings.lockin3.coupling,
                      settings.lockin3.sensitivity, settings.lockin3.reserve, settings.lockin3.time,
                      settings.lockin3.filter, "both")
    print(f"Lockin 3: {lockin3.read_model()} drivers loaded and configured.")
# endregion

# region ----- Set or create current directory where to save files -----
print("\n***** Measurement log *****")
path = f"{experiment.main}\\{experiment.chip}\\{experiment.device}\\{experiment.experiment}\\{experiment.date.strftime('%Y-%m-%d %H.%M.%S')}\\"
try:
    os.chdir(path)  # if path exists, then make it cwd
    print(f"{path} ... found.")
except OSError:  # if path does not exists
    print(f"{path} ... not found. Making directory... ")
    os.makedirs(path)  # make new directory
    os.chdir(path)  # make path cwd
print(f"Current working directory set to: {os.getcwd()}")
# endregion

# region ----- Allocate RAM -----
print("Allocating RAM... ", end="")
data = StabilityDiagram(mode, heater, t, i_h, vg, vb, v_ex, settings)
print("Done.")
# endregion

# region ----- Initialize figures -----
print("Initializing figure... ", end="")
matplotlib.rcParams['path.simplify'] = True
matplotlib.rcParams['path.simplify_threshold'] = 1.0
if settings.tc.address is not None:
    plot0 = PlotObsT(["stage", "shield"], settings.tc.settling_time)
plot1 = PlotStabilityDiagramI(vg, vb)
plot2 = PlotTEStabilityDiagramI(vg, vb)
if mode == 1:
    plot3 = PlotTEStabilityDiagramV(vg, vb)
plt.show(block=False)
plt.pause(.1)
print("Done.")  # endregion

input("""Set heater current source input to OFF, and heater current source output to ON.
Set thermometer(s) current source input to ON, and thermometer(s) current source output to ON.
Unground the device, then press Enter to start measurement...""")
# set the temperature at which needles must be lifted and then lowered again before continuing

no_samples = int((settings.adc.vt_settling_time + settings.adc.vt_measurement_time) / (settings.adc.nplc / settings.adc.line_freq))
no_samples2avg = int(settings.adc.vt_measurement_time / (settings.adc.nplc / settings.adc.line_freq))
current_input_state = False  # flag for heater source when V < 0.004

for idx_t, val_t in enumerate(t):

    # region ----- Set temperature -----
    if settings.tc.address is not None:

        # region ----- Set temperature and wait for thermalization -----
        # set temperature controller setpoint
        tc.set_temperature(output=0, setpoint=val_t)
        tc.set_temperature(output=1, setpoint=val_t) if settings.tc.model == 0 else None

        # select thermalization time between initial and "regular" value
        print(f"Waiting for thermalization at {val_t:04.1f} K...", end="")
        if idx_t == 0:
            settling_time = settings.tc.settling_time_init
        else:
            settling_time = settings.tc.settling_time

        # record data
        k = 0
        t0 = time.time()
        if idx_t > 0:
            setpoint_line.remove()
        plot0.ax.set_xlim([0, settling_time])  # duration must be updated because the initial settling time is different from the regular time
        setpoint_line = plot0.ax.add_line(Line2D(xdata=array([0, settling_time]), ydata=array([val_t, val_t]), color="grey", linewidth=1, linestyle="--"))  # add setpoint
        while time.time() - t0 <= settling_time:
            data.t[idx_t]["tt"].time[k] = time.time() - t0
            data.t[idx_t]["tt"].stage[k] = tc.read_temperature("a")
            data.t[idx_t]["tt"].shield[k] = tc.read_temperature("b")
            plot0.ax.lines[0].set_data(data.t[idx_t]["tt"].time[0:k + 1], data.t[idx_t]["tt"].stage[0:k + 1])
            plot0.ax.lines[1].set_data(data.t[idx_t]["tt"].time[0:k + 1], data.t[idx_t]["tt"].shield[0:k + 1])
            plot0.ax.relim()
            plot0.ax.autoscale_view("y")
            plt.pause(1 / settings.tc.sampling_freq)
            k = k + 1

        # remove trailing zeros from saved data
        data.t[idx_t]["tt"].time = data.t[idx_t]["tt"].time[0:k]
        data.t[idx_t]["tt"].stage = data.t[idx_t]["tt"].stage[0:k]
        data.t[idx_t]["tt"].shield = data.t[idx_t]["tt"].shield[0:k]

        print("Done.")  # endregion

        # save figure to disc
        print("Saving thermalization figure to disc... ", end="")
        plot0.fig.savefig(fname=f"thermalization - {val_t:04.1f} K.png", format="png")
        print("Done.")

    elif settings.tc.address is None:

        input(f"Set temperature to {val_t:04.1f} K, wait for thermalization, then press Enter to continue...")

    # endregion

    # region ----- Set AC bias voltage -----
    print(f"Ramping up AC bias voltage from 0 V to {amplitude2rms(v_ex / v_ex_divider):.3f} Vrms... ", end="")
    if settings.lockin2.address is not None:
        lockin2.sweep_v(0, amplitude2rms(v_ex / v_ex_divider))
    else:
        input(f"Set lockin 2 output to {amplitude2rms(v_ex / v_ex_divider):.3f} Vrms")
    print("Done.")  # endregion

    for idx_i_h, val_i_h in enumerate(i_h):

        mask = ones((len(vg), len(vb)), dtype=bool)  # make mask for plotting

        # region ----- Ramp up heater current -----
        val_i_h_ = 0 if idx_i_h == 0 else i_h[idx_i_h - 1]

        if amplitude2rms(val_i_h / settings.src3.gain) < 0.004:
            if current_input_state is True:
                if settings.src3.address is not None:
                    src3.operation(input_state="off", output_state="on")
                    current_input_state = False
                else:
                    input("Set 'src3' input = off and output = on, then press Enter to continue.")
                    current_input_state = False
            else:
                pass
        else:
            if current_input_state is True:
                pass
            else:
                if settings.src3.address is not None:
                    src3.operation(input_state="on", output_state="on")
                else:
                    input("Set 'src3' input = on and output = on, then press Enter to continue.")
                    current_input_state = True  # to skip next iteration src3 settings

        if settings.lockin1.address is not None:
            print(f"Ramping up heater current from {1e3 * val_i_h_:.1f} mA to {1e3 * val_i_h:.1f} mA... ", end="")
            lockin1.sweep_v(amplitude2rms(val_i_h_ / settings.src3.gain), amplitude2rms(val_i_h / settings.src3.gain))
            print("Done.")
        else:
            input(f"Set lockin 1 output to {amplitude2rms(val_i_h / settings.src3.gain):.1f} Vrms, then press Enter to continue.")
        # endregion

        # region ----- Wait for steady state -----
        t0 = time.time()
        print("Waiting for steady state... ", end="")
        while time.time() - t0 <= heater_settling_time:
            pass
        print("Done.")  # endregion

        for idx_vg, val_vg in enumerate(vg):

            # region ----- Sweep DC gate voltage -----
            val_vg_ = 0 if idx_vg == 0 else vg[idx_vg - 1]
            print(f"Setting DC gate from {val_vg_:.1f} V to {val_vg:.1f} V... ", end="")
            adc.adw.Set_Par(21, 1)  # set ao1 ON
            adc.adw.Set_Par(22, 0)  # set ao2 OFF

            if val_vg_ == val_vg:
                temp_vg = array([val_vg]) / settings.avv1.gain
            else:
                temp_vg = linspace(0 if idx_vg == 0 else vg[idx_vg - 1], vg[idx_vg], vg_steps) / settings.avv1.gain

            adc.adw.SetData_Long(list(adc.voltage2bin(temp_vg)), 21, 1, len(temp_vg))  # set ao1 data
            adc.adw.Set_Par(41, len(temp_vg))  # set length of ao1 array
            adc.adw.Start_Process(4)

            while adc.adw.Process_Status(4) == 1:
                plt.pause(0.01)
            print("Done.")  # endregion

            for idx_vb, val_vb in enumerate(vb):

                # region ----- Sweep DC bias voltage -----
                val_vb_ = 0 if idx_vb == 0 else vb[idx_vb - 1]
                print(f"Setting DC bias from {val_vb_:.4f} V to {val_vb:.4f} V... ", end="")
                adc.adw.Set_Par(21, 0)  # set ao1 OFF
                adc.adw.Set_Par(22, 1)  # set ao2 ON

                if val_vb_ == val_vb:
                    temp_vb = array([val_vb]) / vb_divider
                else:
                    temp_vb = linspace(0 if idx_vb == 0 else vb[idx_vb - 1], vb[idx_vb], vb_steps) / vb_divider

                adc.adw.SetData_Long(list(adc.voltage2bin(temp_vb)), 22, 1, len(temp_vb))  # set ao2 data
                adc.adw.Set_Par(42, len(temp_vb))  # set length of ao2 array
                adc.adw.Start_Process(4)

                while adc.adw.Process_Status(4) == 1:
                    plt.pause(0.01)
                print("Done.")  # endregion

                # region ----- Measure -----
                print("Measuring... ", end="")
                adc.adw.Start_Process(2)

                while adc.adw.Process_Status(2) == 1:
                    plt.pause(1e-3)
                print("Done.")  # endregion

                # region ----- Read and store data locally -----
                print("Reading data from adc... ", end="")
                ai1 = ctypeslib.as_array(adc.adw.GetData_Float(1, no_samples - no_samples2avg, no_samples2avg))
                i1 = adc.bin2voltage(ai1, bits=settings.adc.input_resolution) * settings.lockin1.sensitivity / 10 / settings.avi.gain
                data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["i_2w1"]["x"][idx_vg, idx_vb, 0] = mean(i1)
                data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["i_2w1"]["x"][idx_vg, idx_vb, 1] = std(i1)

                ai2 = ctypeslib.as_array(adc.adw.GetData_Float(2, no_samples - no_samples2avg, no_samples2avg))
                i2 = adc.bin2voltage(ai2, bits=settings.adc.input_resolution) * settings.lockin1.sensitivity / 10 / settings.avi.gain
                data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["i_2w1"]["y"][idx_vg, idx_vb, 0] = mean(i2)
                data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["i_2w1"]["y"][idx_vg, idx_vb, 1] = std(i2)

                ai3 = ctypeslib.as_array(adc.adw.GetData_Float(3, no_samples - no_samples2avg, no_samples2avg))
                i3 = adc.bin2voltage(ai3, bits=settings.adc.input_resolution) / settings.lockin2.sensitivity * 10 / settings.avi.gain
                data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["i_w2"]["x"][idx_vg, idx_vb, 0] = mean(i3)
                data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["i_w2"]["x"][idx_vg, idx_vb, 1] = std(i3)

                ai4 = ctypeslib.as_array(adc.adw.GetData_Float(4, no_samples - no_samples2avg, no_samples2avg))
                i4 = adc.bin2voltage(ai4, bits=settings.adc.input_resolution) / settings.lockin2.sensitivity * 10 / settings.avi.gain
                data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["i_w2"]["y"][idx_vg, idx_vb, 0] = mean(i4)
                data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["i_w2"]["y"][idx_vg, idx_vb, 1] = std(i4)

                ai5 = ctypeslib.as_array(adc.adw.GetData_Float(5, no_samples - no_samples2avg, no_samples2avg))
                i5 = adc.bin2voltage(ai5, bits=settings.adc.input_resolution) / settings.avi.gain
                data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["i_dc"][idx_vg, idx_vb, 0] = mean(i5)
                data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["i_dc"][idx_vg, idx_vb, 1] = std(i5)

                if mode == 1:
                    ai6 = ctypeslib.as_array(adc.adw.GetData_Float(6, no_samples - no_samples2avg, no_samples2avg))
                    v6 = adc.bin2voltage(ai6, bits=settings.adc.input_resolution) / settings.avv2.gain
                    data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["v_dc"][idx_vg, idx_vb, 0] = mean(v6)
                    data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["v_dc"][idx_vg, idx_vb, 1] = std(v6)

                    ai7 = ctypeslib.as_array(adc.adw.GetData_Float(7, no_samples - no_samples2avg, no_samples2avg))
                    v7 = adc.bin2voltage(ai7, bits=settings.adc.input_resolution) * settings.lockin3.sensitivity / 10 / settings.avv2.gain
                    data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["v_2w1"]["x"][idx_vg, idx_vb, 0] = mean(v7)
                    data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["v_2w1"]["x"][idx_vg, idx_vb, 1] = std(v7)

                    ai8 = ctypeslib.as_array(adc.adw.GetData_Float(8, no_samples - no_samples2avg, no_samples2avg))
                    v8 = adc.bin2voltage(ai8, bits=settings.adc.input_resolution) * settings.lockin3.sensitivity / 10 / settings.avv2.gain
                    data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["v_2w1"]["y"][idx_vg, idx_vb, 0] = mean(v8)
                    data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["v_2w1"]["y"][idx_vg, idx_vb, 1] = std(v8)

                print("Done.")  # endregion

                # region ----- Update figures -----
                print("Updating plots... ", end="")

                # update mask
                for i in range(idx_vg+1):
                    for j in range(idx_vb+1):
                        mask[i, j] = False

                # update "stability diagram"
                temp = data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["i_w2"]["x"][:, :, 0]
                plot1.ax00.lines[idx_vg].set_data(vb[0:idx_vb+1], temp[idx_vg, 0:idx_vb+1])
                plot1.ax00.relim()
                plot1.ax00.autoscale_view(scalex=False, scaley=True)
                plot1.ax10.lines[idx_vg].set_data(vb[0:idx_vb+1], log10(abs(temp[idx_vg, 0:idx_vb+1])))
                plot1.ax10.relim()
                plot1.ax10.autoscale_view(scalex=False, scaley=True)
                plot1.ax01.lines[idx_vb].set_data(vg[0:idx_vg+1], temp[0:idx_vg+1, idx_vb])
                plot1.ax01.relim()
                plot1.ax01.autoscale_view(scalex=False, scaley=True)
                plot1.ax11.lines[idx_vb].set_data(vg[0:idx_vg+1], log10(abs(temp[0:idx_vg+1, idx_vb])))
                plot1.ax11.relim()
                plot1.ax11.autoscale_view(scalex=False, scaley=True)
                temp = ma.masked_array(temp.T, mask.T)
                plot1.im02.set_data(temp)
                plot1.im02.set_clim(vmin=nanmin(temp.flatten()), vmax=nanmax(temp.flatten()))
                plot1.im12.set_data(log10(abs(temp)))
                plot1.im12.set_clim(vmin=nanmin(log10(abs(temp.flatten()))), vmax=nanmax(log10(abs(temp.flatten()))))

                # update "thermoelectric stability diagram"
                temp = data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["i_2w1"]["y"][:, :, 0]
                plot2.ax00.lines[idx_vg].set_data(vb[0:idx_vb+1], temp[idx_vg, 0:idx_vb+1])
                plot2.ax00.relim()
                plot2.ax00.autoscale_view(scalex=False, scaley=True)
                plot2.ax10.lines[idx_vg].set_data(vb[0:idx_vb+1], log10(abs(temp[idx_vg, 0:idx_vb+1])))
                plot2.ax10.relim()
                plot2.ax10.autoscale_view(scalex=False, scaley=True)
                plot2.ax01.lines[idx_vb].set_data(vg[0:idx_vg+1], temp[0:idx_vg+1, idx_vb])
                plot2.ax01.relim()
                plot2.ax01.autoscale_view(scalex=False, scaley=True)
                plot2.ax11.lines[idx_vb].set_data(vg[0:idx_vg+1], log10(abs(temp[0:idx_vg+1, idx_vb])))
                plot2.ax11.relim()
                plot2.ax11.autoscale_view(scalex=False, scaley=True)
                temp = ma.masked_array(temp.T, mask.T)
                plot2.im02.set_data(temp)
                plot2.im02.set_clim(vmin=nanmin(temp.flatten()), vmax=nanmax(temp.flatten()))
                plot2.im12.set_data(log10(abs(temp)))
                plot2.im12.set_clim(vmin=nanmin(log10(abs(temp.flatten()))), vmax=nanmax(log10(abs(temp.flatten()))))

                if mode == 1:
                    # update "thermoelectric stability diagram"
                    temp = data.t[idx_t]["sd"][f"h{heater}"][idx_i_h]["v_2w1"]["y"][:, :, 0]
                    plot3.ax00.lines[idx_vg].set_data(vb[0:idx_vb+1], temp[idx_vg, 0:idx_vb+1])
                    plot3.ax00.relim()
                    plot3.ax00.autoscale_view(scalex=False, scaley=True)
                    plot3.ax10.lines[idx_vg].set_data(vb[0:idx_vb+1], log10(abs(temp[idx_vg, 0:idx_vb+1])))
                    plot3.ax10.relim()
                    plot3.ax10.autoscale_view(scalex=False, scaley=True)
                    plot3.ax01.lines[idx_vb].set_data(vg[0:idx_vg+1], temp[0:idx_vg+1, idx_vb])
                    plot3.ax01.relim()
                    plot3.ax01.autoscale_view(scalex=False, scaley=True)
                    plot3.ax11.lines[idx_vb].set_data(vg[0:idx_vg+1], log10(abs(temp[0:idx_vg+1, idx_vb])))
                    plot3.ax11.relim()
                    plot3.ax11.autoscale_view(scalex=False, scaley=True)
                    temp = ma.masked_array(temp.T, mask.T)
                    plot3.im02.set_data(temp)
                    plot3.im02.set_clim(vmin=nanmin(temp.flatten()), vmax=nanmax(temp.flatten()))
                    plot3.im12.set_data(log10(abs(temp)))
                    plot3.im12.set_clim(vmin=nanmin(log10(abs(temp.flatten()))), vmax=nanmax(log10(abs(temp.flatten()))))

                plt.pause(0.1)
                print("Done.")  # endregion

            # region ----- Sweep DC bias back to 0 -----
            val_vb_ = vb[idx_vb]
            print(f"Setting DC bias from {val_vb_:.1f} V to 0 V... ", end="")
            adc.adw.Set_Par(21, 0)  # set ao1 ON
            adc.adw.Set_Par(22, 1)  # set ao2 ON

            temp_vb = linspace(vb[idx_vb], 0, vb_steps) / vb_divider

            adc.adw.SetData_Long(list(adc.voltage2bin(temp_vb)), 22, 1, len(temp_vb))  # set ao2 data
            adc.adw.Set_Par(42, len(temp_vb))  # set length of ao2 array
            adc.adw.Start_Process(4)

            while adc.adw.Process_Status(4) == 1:
                plt.pause(0.01)
            print("Done.")  # endregion

            # region ----- Save data to disc -----
            print("Saving data to disc... ", end="")
            experiment.data = data
            if os.path.exists(experiment.filename):
                if os.path.exists(experiment.backupname):
                    os.remove(experiment.backupname)
                os.rename(experiment.filename, experiment.backupname)
            with open(experiment.filename, "wb") as file:
                pickle.dump(experiment, file)
            print("Done.")

            print("Saving figures to disc... ", end="")
            plot1.fig.savefig(fname=f"{experiment.experiment} Iw2 - {val_t:04.1f} K - {1e3*val_i_h:04.1f} mA.png", format="png")
            plot2.fig.savefig(fname=f"{experiment.experiment} I2w1 - {val_t:04.1f} K - {1e3 * val_i_h:04.1f} mA.png", format="png")
            if mode == 1:
                plot3.fig.savefig(fname=f"{experiment.experiment} V2w1 - {val_t:04.1f} K - {1e3 * val_i_h:04.1f} mA.png", format="png")
            print("Done.")
            # endregion

    # region ----- Sweep DC gate voltage back to 0 -----
    print(f"Setting DC gate from {vg[idx_vg]:.4f} V to {0:.4f} V... ", end="")
    adc.adw.Set_Par(21, 1)  # set ao1 ON
    adc.adw.Set_Par(22, 0)  # set ao2 OFF

    temp_vg = linspace(0, vg[idx_vg], vg_steps) / settings.avv1.gain

    adc.adw.SetData_Long(list(adc.voltage2bin(temp_vg)), 21, 1, len(temp_vg))  # set ao1 data
    adc.adw.Set_Par(41, len(temp_vg))  # set length of ao1 array
    adc.adw.Start_Process(4)

    while adc.adw.Process_Status(4) == 1:
        plt.pause(0.01)
    print("Done.")  # endregion

    # region ----- Ramp down heater current -----
    if settings.lockin1.address is not None:
        print(f"Ramping down heater current from {1e3*val_i_h:04.1f} mA to 0 mA... ", end="")
        lockin1.sweep_v(amplitude2rms(val_i_h / settings.src3.gain), 0)
        print("Done.")
    else:
        input(f"Set lockin 1 output to 0 V, then press Enter to continue.")
    if settings.src3.address is not None:
        src3.set_output_state("off")
    # endregion

    # region ----- Ramp down AC bias voltage -----
    print(f"Ramping down AC bias voltage from {amplitude2rms(v_ex / v_ex_divider):.3f} Vrms to 0 Vrms... ", end="")
    if settings.lockin2.address is not None:
        lockin2.sweep_v(amplitude2rms(v_ex / v_ex_divider), 0)
    else:
        input(f"Set lockin 2 output to 0 Vrms")
    print("Done.")  # endregion

input("Measurement complete. Ground the device then press Enter to terminate.")
plt.show()
