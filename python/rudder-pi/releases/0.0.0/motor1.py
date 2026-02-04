import RPi.GPIO as GPIO
import time

# GPIO Nummerierung
GPIO.setmode(GPIO.BCM)

ESC_PIN = 17
BTN_UP = 22
BTN_DOWN = 23

# GPIO Setup
GPIO.setup(ESC_PIN, GPIO.OUT)
GPIO.setup(BTN_UP, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BTN_DOWN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# PWM Setup (50 Hz für ESC/Servo)
pwm = GPIO.PWM(ESC_PIN, 50)
pwm.start(7.5)  # Neutralstellung

# Duty-Cycle Grenzen
duty = 7.5       # Neutral
DUTY_MIN = 5.0
DUTY_MAX = 10.0
STEP = 0.1       # Schrittweite

print("ESC Steuerung gestartet")

try:
    while True:
        if not GPIO.input(BTN_UP):
            duty += STEP
            duty = min(duty, DUTY_MAX)
            pwm.ChangeDutyCycle(duty)
            print(f"Schneller: {duty:.1f}")
            time.sleep(0.2)

        if not GPIO.input(BTN_DOWN):
            duty -= STEP
            duty = max(duty, DUTY_MIN)
            pwm.ChangeDutyCycle(duty)
            print(f"Langsamer: {duty:.1f}")
            time.sleep(0.2)

        time.sleep(0.05)

except KeyboardInterrupt:
    print("Beende Programm")

finally:
    pwm.ChangeDutyCycle(7.5)  # Neutral
    time.sleep(0.5)
    pwm.stop()
    GPIO.cleanup()
