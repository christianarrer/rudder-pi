import RPi.GPIO as GPIO
import time
import curses

ESC_PIN = 17

# PWM Werte (ESC typisch)
DUTY_MIN = 5.0     # langsam / stop
DUTY_MAX = 10.0    # vollgas
DUTY_NEUTRAL = 7.5
STEP = 0.1

def main(stdscr):
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(ESC_PIN, GPIO.OUT)

    pwm = GPIO.PWM(ESC_PIN, 50)  # 50 Hz
    duty = DUTY_NEUTRAL
    pwm.start(duty)

    curses.cbreak()
    stdscr.keypad(True)
    stdscr.nodelay(True)

    stdscr.addstr(0, 0, "Pfeil ↑ schneller | Pfeil ↓ langsamer | q oder ESC = Ende")
    stdscr.addstr(2, 0, f"PWM Duty: {duty:.1f}")

    try:
        while True:
            key = stdscr.getch()

            if key == curses.KEY_UP:
                duty = min(duty + STEP, DUTY_MAX)
                pwm.ChangeDutyCycle(duty)

            elif key == curses.KEY_DOWN:
                duty = max(duty - STEP, DUTY_MIN)
                pwm.ChangeDutyCycle(duty)

            elif key == ord('q') or key == 27:  # q oder ESC
                break

            stdscr.addstr(2, 0, f"PWM Duty: {duty:.1f}   ")
            time.sleep(0.05)

    finally:
        pwm.ChangeDutyCycle(DUTY_NEUTRAL)
        time.sleep(0.5)
        pwm.stop()
        GPIO.cleanup()

# curses starten
curses.wrapper(main)
