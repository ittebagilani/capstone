#include <SPI.h>
#include <TMCStepper.h>
#include <math.h>
#include <stdlib.h>
#include <ctype.h>

// ========================================
// MOTOR A
// ========================================

#define STEP_A 25
#define DIR_A  26
#define EN_A   27
#define CS_A   5

// ========================================
// MOTOR B
// ========================================

#define STEP_B 32
#define DIR_B  33
#define EN_B   13
#define CS_B   14

// ========================================
// SHARED SPI
// ========================================

#define SCK_PIN  18
#define MISO_PIN 19
#define MOSI_PIN 23

#define R_SENSE 0.075f

TMC5160Stepper driverA(CS_A, R_SENSE);
TMC5160Stepper driverB(CS_B, R_SENSE);

// ========================================
// REED SENSORS
// ========================================

#define X_HOME_PIN 22
#define Y_HOME_PIN 21

#define SENSOR_TRIGGERED LOW

// ========================================
// MOTOR AND HOMING SETTINGS
// ========================================

const int MOTOR_CURRENT = 800;
const int MICROSTEPS = 16;
// VERIFY pulley and belt dimensions before using millimetre commands.
const float MOTOR_STEPS_PER_REV = 200.0f;
const float BELT_PITCH_MM = 2.0f;
const int PULLEY_TEETH = 20;
const float X_STEPS_PER_MM = MOTOR_STEPS_PER_REV * MICROSTEPS / (BELT_PITCH_MM * PULLEY_TEETH);
const float Y_STEPS_PER_MM = X_STEPS_PER_MM;
// Conservative TEST limits, not the measured gantry dimensions.
// Increase only after measuring safe travel from the homed position.
const float X_MAX_MM = 1000000.0f;
const float Y_MAX_MM = 1000000.0f;
const long X_MAX_STEPS = (long)(X_MAX_MM * X_STEPS_PER_MM);
const long Y_MAX_STEPS = (long)(Y_MAX_MM * Y_STEPS_PER_MM);
const int MOVE_DELAY = 1000;
const unsigned long REPORT_MS = 500;


const int FAST_DELAY = 1000;
const int SLOW_DELAY = 3000;

const long MAX_HOME_STEPS = 48000;
const long MAX_RELEASE_STEPS = 3000;
const long BACKOFF_STEPS = 160;

const unsigned long HOME_TIMEOUT = 120000;
const unsigned long DEBOUNCE_MS = 20;

// ========================================
// POSITION AND STATE
// ========================================

// Coordinates in HALF Cartesian microsteps (one motor pulse can move both axes).
long xPosition = 0;
long yPosition = 0;

bool xHomed = false;
bool yHomed = false;

bool stopped = false;
bool driversReady = false;

// ========================================
// MOTOR ENABLE / DISABLE
// ========================================

void enableMotors() {

  digitalWrite(EN_A, LOW);
  digitalWrite(EN_B, LOW);
}

void disableMotors() {

  digitalWrite(STEP_A, LOW);
  digitalWrite(STEP_B, LOW);

  digitalWrite(EN_A, HIGH);
  digitalWrite(EN_B, HIGH);
}

// ========================================
// ABORT MOTION
// ========================================

void abortMotion(const char* reason) {

  disableMotors();

  stopped = true;

  // Position is no longer guaranteed
  xHomed = false;
  yHomed = false;

  Serial.print("STOPPED: ");
  Serial.println(reason);
}

// ========================================
// EMERGENCY STOP CHECK
// ========================================

bool checkStop() {

  while (Serial.available()) {

    char c = Serial.read();

    if (c == 'X' || c == 'x' || c == 'S' || c == 's') {

      abortMotion("Homing aborted by operator");
      while (Serial.available()) Serial.read();
      return true;
    }
  }

  return stopped;
}

// ========================================
// SENSOR READING
// ========================================

bool sensorTriggered(int pin) {

  return digitalRead(pin) == SENSOR_TRIGGERED;
}

// ========================================
// SENSOR DEBOUNCING
// ========================================

bool sensorStable(int pin, bool target) {

  unsigned long start = millis();

  while (millis() - start < DEBOUNCE_MS) {

    if (checkStop()) return false;

    if (sensorTriggered(pin) != target) {
      return false;
    }

    delay(1);
  }

  return !stopped;
}

// ========================================
// COREXY DIRECTION CONTROL
// ========================================

void setDirection(char axis, bool towardHome) {

  if (axis == 'X') {

    if (towardHome) {

      // RIGHT
      digitalWrite(DIR_A, HIGH);
      digitalWrite(DIR_B, HIGH);

    } else {

      // LEFT
      digitalWrite(DIR_A, LOW);
      digitalWrite(DIR_B, LOW);
    }

  } else if (axis == 'Y') {

    if (towardHome) {

      // BACKWARD
      digitalWrite(DIR_A, LOW);
      digitalWrite(DIR_B, HIGH);

    } else {

      // FORWARD
      digitalWrite(DIR_A, HIGH);
      digitalWrite(DIR_B, LOW);
    }
  }

  delayMicroseconds(20);
}

// ========================================
// GENERATE ONE COREXY STEP
// ========================================

void stepBoth(int stepDelay) {

  digitalWrite(STEP_A, HIGH);
  digitalWrite(STEP_B, HIGH);

  delayMicroseconds(stepDelay);

  digitalWrite(STEP_A, LOW);
  digitalWrite(STEP_B, LOW);

  delayMicroseconds(stepDelay);
}

// ========================================
// MOVE UNTIL SENSOR REACHES TARGET STATE
// ========================================

bool moveUntilSensor(
  char axis,
  int sensorPin,
  bool towardHome,
  bool targetState,
  int stepDelay,
  long maxSteps,
  unsigned long homeStart
) {

  setDirection(axis, towardHome);

  for (long i = 0; i <= maxSteps; i++) {

    if (checkStop()) return false;

    if (millis() - homeStart >= HOME_TIMEOUT) {

      abortMotion("Homing timeout");
      return false;
    }

    // Stop stepping while confirming sensor
    if (sensorTriggered(sensorPin) == targetState) {

      if (sensorStable(sensorPin, targetState)) {
        return true;
      }

      if (stopped) return false;

      // Unstable reading: retry without moving
      i--;
      continue;
    }

    if (i == maxSteps) break;

    stepBoth(stepDelay);
  }

  abortMotion("Maximum homing travel exceeded");
  return false;
}

// ========================================
// MOVE A FIXED NUMBER OF STEPS
// ========================================

bool moveFixed(
  char axis,
  bool towardHome,
  long steps,
  int stepDelay
) {

  setDirection(axis, towardHome);

  for (long i = 0; i < steps; i++) {

    if (checkStop()) return false;

    stepBoth(stepDelay);
  }

  return true;
}

// ========================================
// PRINT SENSOR STATUS
// ========================================

void printSensors() {

  Serial.print("X sensor: ");

  Serial.print(
    sensorTriggered(X_HOME_PIN)
    ? "TRIGGERED" : "OPEN"
  );

  Serial.print(" | Y sensor: ");

  Serial.println(
    sensorTriggered(Y_HOME_PIN)
    ? "TRIGGERED" : "OPEN"
  );
}

// ========================================
// HOME INDIVIDUAL AXIS
// ========================================

bool homeAxis(char axis) {

  int sensorPin = (axis == 'X')
                  ? X_HOME_PIN
                  : Y_HOME_PIN;

  unsigned long homeStart = millis();

  if (axis == 'X') xHomed = false;
  else yHomed = false;

  Serial.println();
  Serial.print("Homing axis: ");
  Serial.println(axis);

  // --------------------------------------
  // PHASE 1: INITIAL APPROACH
  // --------------------------------------

  if (!sensorTriggered(sensorPin)) {

    Serial.println("Phase 1: Seeking sensor");

    if (!moveUntilSensor(
      axis,
      sensorPin,
      true,
      true,
      FAST_DELAY,
      MAX_HOME_STEPS,
      homeStart
    )) return false;

  } else {

    Serial.println("Sensor already triggered");
  }

  Serial.println("Sensor detected");
  delay(200);

  // --------------------------------------
  // PHASE 2: RELEASE SENSOR
  // --------------------------------------

  Serial.println("Phase 2: Backing away");

  if (!moveUntilSensor(
    axis,
    sensorPin,
    false,
    false,
    FAST_DELAY,
    MAX_RELEASE_STEPS,
    homeStart
  )) return false;

  // --------------------------------------
  // PHASE 3: ADDITIONAL CLEARANCE
  // --------------------------------------

  if (!moveFixed(
    axis,
    false,
    BACKOFF_STEPS,
    FAST_DELAY
  )) return false;

  if (!sensorStable(sensorPin, false)) {

    if (!stopped) {
      abortMotion("Sensor failed to remain released");
    }

    return false;
  }

  delay(200);

  // --------------------------------------
  // PHASE 4: SLOW PRECISION APPROACH
  // --------------------------------------

  Serial.println("Phase 3: Slow approach");

  if (!moveUntilSensor(
    axis,
    sensorPin,
    true,
    true,
    SLOW_DELAY,
    MAX_RELEASE_STEPS + BACKOFF_STEPS,
    homeStart
  )) return false;

  // --------------------------------------
  // PHASE 5: SET ZERO
  // --------------------------------------

  if (axis == 'X') {

    xPosition = 0;
    xHomed = true;

  } else {

    yPosition = 0;
    yHomed = true;
  }

  Serial.print(axis);
  Serial.println(" axis homed successfully");

  return true;
}

// ========================================
// START HOMING
// ========================================

bool prepareHoming() {

  if (!driversReady) {

    Serial.println("Driver communication error");
    return false;
  }

  stopped = false;

  enableMotors();

  delay(100);

  return !checkStop();
}

// ========================================
// HOME BOTH AXES
// ========================================

void homeAll() {

  xHomed = false;
  yHomed = false;

  if (!prepareHoming()) return;

  Serial.println("Starting automatic homing");

  if (!homeAxis('Y')) return;
  if (!homeAxis('X')) return;

  Serial.println();
  Serial.println("========================");
  Serial.println("HOMING COMPLETE");
  Serial.println("Position: X = 0, Y = 0");
  Serial.println("========================");

  printSensors();
}

// ========================================
// PRINT POSITION
// ========================================

void printPosition() {
  if (!xHomed || !yHomed) {
    Serial.println("Position unknown - send H to home");
    return;
  }
  Serial.printf("X: %.1f steps (%.3f mm) | Y: %.1f steps (%.3f mm)\n",
                xPosition / 2.0, xPosition / (2.0 * X_STEPS_PER_MM),
                yPosition / 2.0, yPosition / (2.0 * Y_STEPS_PER_MM));
}

// Motor-event rate, not Cartesian mm/s. Conservative initial settings.
const float START_RATE = 100.0f;
float MAX_RATE = 500.0f;
float ACCEL = 800.0f; // motor events/s^2
const unsigned PULSE_US = 5;
bool moving = false;
bool stopRequested = false;
long targetX, targetY, countA, countB, total, done, errA, errB;
int signA, signB;
float rate = START_RATE;
unsigned long nextPulse, lastReport;
char commandBuffer[64];
size_t commandLength = 0;
bool commandOverflow = false;

// Board calibration is RAM-only. H8 is assumed closest to home.
// Set these to measured values after calibration if desired.
const double DEFAULT_H8_X = -1.0;
const double DEFAULT_H8_Y = -1.0;
const double DEFAULT_PITCH_X = 35.0;
const double DEFAULT_PITCH_Y = 35.0;
double boardX = DEFAULT_H8_X, boardY = DEFAULT_H8_Y;
double pitchX = DEFAULT_PITCH_X, pitchY = DEFAULT_PITCH_Y;

bool boardFits(double x, double y, double px, double py) {
  return isfinite(x) && isfinite(y) && isfinite(px) && isfinite(py) &&
         x >= 0 && y >= 0 && px > 0 && py > 0 &&
         x + 7*px <= X_MAX_STEPS / (double)X_STEPS_PER_MM &&
         y + 7*py <= Y_MAX_STEPS / (double)Y_STEPS_PER_MM;
}

void printBoard() {
  Serial.printf("H8=(%.3f, %.3f), pitch=(%.3f, %.3f) mm\n", boardX, boardY, pitchX, pitchY);
  Serial.printf("A8=(%.3f, %.3f), H1=(%.3f, %.3f), A1=(%.3f, %.3f)\n",
      boardX+7*pitchX, boardY, boardX, boardY+7*pitchY,
      boardX+7*pitchX, boardY+7*pitchY);
  Serial.println(boardFits(boardX,boardY,pitchX,pitchY) ? "Board mapping valid" : "Board not calibrated or outside travel limits");
}

void printHelp() {
  Serial.println("H: Home Y then X | P: Position | ?: Help");
  Serial.println("GOTO x y: Absolute mm | GOM x y: Absolute microsteps");
  Serial.println("L/R/F/B: Jog | M L 800: Relative steps | D F 10: Relative mm");
  Serial.println("S: Controlled stop | X: Immediate abort; rehome required");
  Serial.println("BOARD x y px py: Set H8 centre and square pitches (mm)");
  Serial.println("MARK H8: Teach H8 from current position | MAP: Show board");
  Serial.println("SQUARE E4: Go to square centre | RATE n | ACCEL n");
  Serial.printf("Rate %.0f events/s | Accel %.0f events/s^2\n", MAX_RATE, ACCEL);
  Serial.printf("Travel limits X %.2f Y %.2f mm\n", X_MAX_MM, Y_MAX_MM);
}

void finishMove(const char* reason) {
  moving = false;
  stopRequested = false;
  Serial.println(reason);
  printPosition();
}

bool ready() {
  if (!driversReady || stopped || !xHomed || !yHomed || moving) {
    Serial.println("Motion locked: home both axes and stop first");
    return false;
  }
  return true;
}

void startTarget(long tx, long ty) {
  if (!ready()) return;
  if (tx < 0 || ty < 0 || tx > 2*X_MAX_STEPS || ty > 2*Y_MAX_STEPS) {
    Serial.println("Target outside software limits"); return;
  }
  long dx = tx-xPosition, dy = ty-yPosition;
  // A = -X + Y, B = -X - Y. Stored Cartesian coordinates are doubled.
  if ((dx-dy)%2 || (dx+dy)%2) {
    Serial.println("Target not on motor step lattice"); return;
  }
  long da = (-dx+dy)/2, db = (-dx-dy)/2;
  countA = labs(da); countB = labs(db);
  total = countA > countB ? countA : countB;
  if (!total) { finishMove("Already at target"); return; }
  signA = da >= 0 ? 1 : -1; signB = db >= 0 ? 1 : -1;
  digitalWrite(DIR_A, signA > 0 ? HIGH : LOW);
  digitalWrite(DIR_B, signB > 0 ? HIGH : LOW);
  targetX = tx; targetY = ty;
  errA = errB = total/2; done = 0;
  rate = START_RATE; stopRequested = false;
  nextPulse = micros()+20; lastReport = millis(); moving = true;
  Serial.println("Starting interpolated motion");
}

void startAbsolute(double x, double y, bool mm) {
  double sx = mm ? x*X_STEPS_PER_MM : x;
  double sy = mm ? y*Y_STEPS_PER_MM : y;
  if (!isfinite(sx) || !isfinite(sy) || sx < 0 || sy < 0 ||
      sx > X_MAX_STEPS || sy > Y_MAX_STEPS ||
      (!mm && (floor(sx)!=sx || floor(sy)!=sy))) {
    Serial.println("Invalid absolute target"); return;
  }
  startTarget(2*lround(sx), 2*lround(sy));
}

void startMove(char d, bool jog, bool mm, double amount) {
  if (!ready()) return;
  bool x = d=='L' || d=='R';
  if (!x && d!='F' && d!='B') { Serial.println("Invalid direction"); return; }
  int sign = (d=='L'||d=='F') ? 1 : -1;
  long pos = x ? xPosition : yPosition;
  long limit = 2*(x ? X_MAX_STEPS : Y_MAX_STEPS);
  long available = sign>0 ? limit-pos : pos;
  // Cardinal movement uses paired pulses, so round down to a full step.
  double steps = mm ? amount*(x ? X_STEPS_PER_MM : Y_STEPS_PER_MM) : amount;
  if (!jog && (!isfinite(steps) || steps<=0 || steps>available/2.0 || (!mm && floor(steps)!=steps))) {
    Serial.println("Invalid relative distance or outside limits"); return;
  }
  long delta = 2*(jog ? available/2 : lround(steps))*sign;
  startTarget(x ? pos+delta : xPosition, x ? yPosition : pos+delta);
}

void serviceMotion() {
  if (!moving || (int32_t)(micros()-nextPulse)<0) return;
  if (stopRequested && rate<=START_RATE) { finishMove("Stopped by operator"); return; }
  // Centered integer DDA: independent motor pulses along one line.
  errA += countA; errB += countB;
  bool a = errA >= total, b = errB >= total;
  if (a) errA -= total;
  if (b) errB -= total;
  int da = a ? signA : 0, db = b ? signB : 0;
  long dx = -da-db, dy = da-db;
  long nx = xPosition+dx, ny = yPosition+dy;
  if (nx<0 || ny<0 || nx>2*X_MAX_STEPS || ny>2*Y_MAX_STEPS) {
    finishMove("Software limit: interpolated pulse rejected"); return;
  }
  // Check net direction, not tiny DDA cross-axis oscillations.
  if ((targetX<xPosition && dx<0 && sensorTriggered(X_HOME_PIN)) ||
      (targetY<yPosition && dy<0 && sensorTriggered(Y_HOME_PIN))) {
    moving=false; abortMotion("Home sensor active during approach; rehome"); return;
  }
  if (a) digitalWrite(STEP_A,HIGH);
  if (b) digitalWrite(STEP_B,HIGH);
  delayMicroseconds(PULSE_US);
  if (a) digitalWrite(STEP_A,LOW);
  if (b) digitalWrite(STEP_B,LOW);
  xPosition=nx; yPosition=ny; ++done;
  if (done==total) { finishMove("Target reached"); return; }
  float up = sqrtf(rate*rate+2*ACCEL);
  float down = sqrtf(fmaxf(START_RATE*START_RATE,rate*rate-2*ACCEL));
  float braking = sqrtf(START_RATE*START_RATE+2*ACCEL*(total-done-1));
  rate = stopRequested ? down : fminf(MAX_RATE,fminf(up,braking));
  nextPulse=micros()+(unsigned long)(1000000.0f/rate);
  if (millis()-lastReport>=REPORT_MS) { lastReport=millis(); printPosition(); }
}

void handleCommand(char* line) {
  while (isspace((unsigned char)*line)) ++line;
  if (!*line) return;
  size_t n = strlen(line);
  while (n && isspace((unsigned char)line[n-1])) line[--n] = 0;
  if (strcmp(line,"X")==0) {
    moving=false; stopRequested=false; abortMotion("Manual emergency stop"); return;
  }
  if (strcmp(line,"S")==0) { if (moving) stopRequested=true; return; }
  if (moving) {
    if (strcmp(line,"P")==0) { printPosition(); return; }
    Serial.println("Busy; send S or X to stop"); return;
  }
  if (n == 1) {
    switch (*line) {
      case 'H': homeAll(); return;
      case 'P': printPosition(); printSensors(); return;
      case '?': printHelp(); return;
      case 'L': case 'R': case 'F': case 'B':
        startMove(*line, true, false, 0); return;
    }
  }
  if (strcmp(line,"MAP")==0) { printBoard(); return; }
  if (strcmp(line,"MARK H8")==0) {
    if (!ready()) return;
    double x=xPosition/(2.0*X_STEPS_PER_MM), y=yPosition/(2.0*Y_STEPS_PER_MM);
    if (!boardFits(x,y,pitchX,pitchY)) { Serial.println("Board outside travel limits; calibration unchanged"); return; }
    boardX=x; boardY=y; printBoard(); return;
  }
  if (strncmp(line,"BOARD ",6)==0) {
    double v[4]; char* p=line+6; char* end;
    for (int i=0;i<4;i++) {
      v[i]=strtod(p,&end);
      if (end==p || (i<3 && !isspace((unsigned char)*end))) { Serial.println("Usage: BOARD x y pitchX pitchY"); return; }
      p=end;
    }
    if (*p || !boardFits(v[0],v[1],v[2],v[3])) { Serial.println("Invalid board or outside travel limits"); return; }
    boardX=v[0]; boardY=v[1]; pitchX=v[2]; pitchY=v[3]; printBoard(); return;
  }
  if (strncmp(line,"SQUARE ",7)==0) {
    const char* q=line+7;
    if (strlen(q)!=2 || q[0]<'A' || q[0]>'H' || q[1]<'1' || q[1]>'8') { Serial.println("Usage: SQUARE E4"); return; }
    if (!boardFits(boardX,boardY,pitchX,pitchY)) { Serial.println("Calibrate board first"); return; }
    if (!ready()) return;
    double x=boardX+('H'-q[0])*pitchX, y=boardY+('8'-q[1])*pitchY;
    Serial.printf("Square %s: (%.3f, %.3f) mm\n",q,x,y);
    startAbsolute(x,y,true); return;
  }
  if (strncmp(line,"RATE ",5)==0 || strncmp(line,"ACCEL ",6)==0) {
    bool isRate=line[0]=='R'; char* p=line+(isRate?5:6); char* end;
    double v=strtod(p,&end);
    if (end==p || *end || !isfinite(v) || v<(isRate?100:100) || v>(isRate?2000:3000)) {
      Serial.println("RATE range 100..2000; ACCEL range 100..3000"); return;
    }
    if (isRate) MAX_RATE=v; else ACCEL=v;
    Serial.printf("Rate %.0f events/s | Accel %.0f events/s^2\n",MAX_RATE,ACCEL); return;
  }
  bool gotoMM = strncmp(line, "GOTO ", 5) == 0;
  bool gotoSteps = strncmp(line, "GOM ", 4) == 0;
  if (gotoMM || gotoSteps) {
    char *p = line + (gotoMM ? 5 : 4), *end;
    double x = strtod(p, &end);
    if (end == p || !isspace((unsigned char)*end)) {
      Serial.println("Usage: GOTO x y or GOM x y"); return;
    }
    p = end;
    double y = strtod(p, &end);
    if (end == p || *end || !isfinite(x) || !isfinite(y)) {
      Serial.println("Invalid absolute coordinates"); return;
    }
    startAbsolute(x, y, gotoMM);
    return;
  }
  if ((*line == 'M' || *line == 'D') && isspace((unsigned char)line[1])) {
    char* p = line + 1;
    while (isspace((unsigned char)*p)) ++p;
    if (!*p) { Serial.println("Missing direction"); return; }
    char direction = *p++;
    if (!isspace((unsigned char)*p)) { Serial.println("Missing distance"); return; }
    char* end;
    double value = strtod(p, &end);
    if (end == p || *end || !isfinite(value) || value <= 0) {
      Serial.println("Invalid distance"); return;
    }
    startMove(direction, false, *line == 'D', value);
    return;
  }
  Serial.println("Invalid command; send ? for help");
}

void serviceSerial() {
  for (int budget=0; budget<64 && Serial.available(); ++budget) {
    char c = toupper((unsigned char)Serial.read());
    if (c == '\r' || c == '\n') {
      if (commandOverflow) Serial.println("Command too long");
      else if (commandLength) {
        commandBuffer[commandLength] = 0;
        commandLength = 0;
        handleCommand(commandBuffer);
      }
      commandLength = 0;
      commandOverflow = false;
    } else if (!commandOverflow) {
      if (commandLength < sizeof(commandBuffer)-1) commandBuffer[commandLength++] = c;
      else commandOverflow = true;
    }
  }
}

// ========================================
// SETUP
// ========================================

void setup() {

  Serial.begin(115200);

  pinMode(STEP_A, OUTPUT);
  pinMode(DIR_A, OUTPUT);
  pinMode(EN_A, OUTPUT);
  pinMode(CS_A, OUTPUT);

  pinMode(STEP_B, OUTPUT);
  pinMode(DIR_B, OUTPUT);
  pinMode(EN_B, OUTPUT);
  pinMode(CS_B, OUTPUT);

  digitalWrite(CS_A, HIGH);
  digitalWrite(CS_B, HIGH);

  disableMotors();

  pinMode(X_HOME_PIN, INPUT_PULLUP);
  pinMode(Y_HOME_PIN, INPUT_PULLUP);

  SPI.begin(SCK_PIN, MISO_PIN, MOSI_PIN);

  // Exact working initialization: Motor A
  driverA.begin();
  driverA.toff(4);
  driverA.rms_current(MOTOR_CURRENT);
  driverA.microsteps(MICROSTEPS);
  driverA.en_pwm_mode(false);

  // Exact working initialization: Motor B
  driverB.begin();
  driverB.toff(4);
  driverB.rms_current(MOTOR_CURRENT);
  driverB.microsteps(MICROSTEPS);
  driverB.en_pwm_mode(false);

  int resultA = driverA.test_connection();
  int resultB = driverB.test_connection();

  Serial.print("Driver A connection: ");
  Serial.println(resultA);

  Serial.print("Driver B connection: ");
  Serial.println(resultB);

  driversReady = (resultA == 0 && resultB == 0);

  if (!driversReady) {

    Serial.println("WARNING: Check SPI wiring");
  }

  Serial.println();
  Serial.println("========================");
  Serial.println("COREXY MOTION CONTROLLER");
  printHelp();
  Serial.println("Send H before moving. Serial Monitor: 115200, Newline.");

  printSensors();
}

// ========================================
// MAIN LOOP
// ========================================

void loop() {
  serviceSerial();
  serviceMotion();
}
