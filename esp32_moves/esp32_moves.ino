// Receives UCI moves ("e2e4", "e7e8q") over WiFi (TCP port 3333) or USB serial,
// replies "DONE <move>" or "ERR <line>".
#include <WiFi.h>
#include <ESPmDNS.h>

const char *WIFI_SSID = "your-wifi";
const char *WIFI_PASS = "your-password";
#define LED 2  // onboard LED on most ESP32 dev boards; change if yours differs

WiFiServer server(3333);
WiFiClient client;

bool isMove(const String &m) {
  if (m.length() != 4 && m.length() != 5) return false;
  for (int i = 0; i < 4; i += 2)
    if (m[i] < 'a' || m[i] > 'h' || m[i + 1] < '1' || m[i + 1] > '8') return false;
  return m.length() == 4 || strchr("qrbn", m[4]);
}

void handle(Stream &io) {
  if (!io.available()) return;
  String cmd = io.readStringUntil('\n');
  cmd.trim();
  if (cmd.isEmpty()) return;
  if (!isMove(cmd)) { io.println("ERR " + cmd); return; }

  // ponytail: blink stands in for moving the piece; put motor/magnet code here
  digitalWrite(LED, HIGH); delay(300); digitalWrite(LED, LOW);
  io.println("DONE " + cmd);
}

void setup() {
  Serial.begin(115200);
  pinMode(LED, OUTPUT);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  MDNS.begin("chessbot");  // reachable as chessbot.local
  server.begin();
  Serial.print("\nREADY at ");
  Serial.println(WiFi.localIP());
}

void loop() {
  if (!client || !client.connected()) client = server.available();  // one Pi at a time
  if (client) handle(client);
  handle(Serial);  // USB still works for debugging
}
