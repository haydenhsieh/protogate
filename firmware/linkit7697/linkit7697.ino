#include <ArduinoJson.h>
#include <SPI.h>
#include <Wire.h>
#include <LWiFi.h>
#include <LWiFiServer.h>
#include <LWiFiClient.h>

#if __has_include("wifi_config.h")
#include "wifi_config.h"
#else
#include "wifi_config.example.h"
#endif

static const uint32_t SERIAL_BAUD = 115200;
static const uint16_t WIFI_PORT = 4242;
static const size_t MAX_LINE = 768;
static const size_t MAX_TRANSFER = 96;
LWiFiServer wifiServer(WIFI_PORT);
LWiFiClient wifiClient;

static void setError(JsonDocument &response, const char *message) {
  response["ok"] = false;
  response["error"] = message;
}

static bool validByteArray(JsonVariantConst value, size_t maxLength) {
  if (!value.is<JsonArrayConst>()) return false;
  JsonArrayConst bytes = value.as<JsonArrayConst>();
  if (bytes.size() > maxLength) return false;
  for (JsonVariantConst byte : bytes) {
    if (!byte.is<int>() || byte.as<int>() < 0 || byte.as<int>() > 255) return false;
  }
  return true;
}

static void handleRequest(JsonDocument &request, JsonDocument &response) {
  response["ok"] = true;
  if (request.containsKey("id")) response["id"] = request["id"];
  const char *op = request["op"] | "";

  if (!strcmp(op, "system.info")) {
    response["board"] = "LinkIt 7697";
    response["firmware"] = "ProtoGate 0.1.0";
    JsonArray caps = response["capabilities"].to<JsonArray>();
    caps.add("i2c"); caps.add("spi"); caps.add("serial"); caps.add("wifi-tcp");
    return;
  }

  if (!strcmp(op, "i2c.scan")) {
    int first = request["address_start"] | 0x08;
    int last = request["address_end"] | 0x77;
    if (first < 0x08 || last > 0x77 || first > last) {
      setError(response, "invalid I2C address range"); return;
    }
    JsonArray devices = response["devices"].to<JsonArray>();
    for (int address = first; address <= last; ++address) {
      Wire.beginTransmission(address);
      if (Wire.endTransmission() == 0) devices.add(address);
    }
    return;
  }

  if (!strcmp(op, "i2c.write") || !strcmp(op, "i2c.write_read") ||
      !strcmp(op, "i2c.read")) {
    int address = request["address"] | -1;
    if (address < 0x08 || address > 0x77) { setError(response, "invalid I2C address"); return; }
    bool hasWrite = strcmp(op, "i2c.read") != 0;
    JsonVariantConst bytes = request[hasWrite ? "data" : "write"];
    if (hasWrite && !validByteArray(bytes, MAX_TRANSFER)) { setError(response, "invalid I2C payload"); return; }
    int length = request["length"] | 0;
    bool hasRead = strcmp(op, "i2c.write") != 0;
    if (hasRead && (length < 0 || length > (int)MAX_TRANSFER)) { setError(response, "invalid I2C read length"); return; }
    Wire.beginTransmission(address);
    if (hasWrite) for (JsonVariantConst byte : bytes.as<JsonArrayConst>()) Wire.write(byte.as<uint8_t>());
    uint8_t status = Wire.endTransmission(!hasRead);
    if (status != 0) { setError(response, "I2C transmission failed"); return; }
    if (!hasRead) { response["written"] = bytes.as<JsonArrayConst>().size(); return; }
    int received = Wire.requestFrom((uint8_t)address, (uint8_t)length);
    JsonArray data = response["data"].to<JsonArray>();
    while (Wire.available()) data.add(Wire.read());
    if (received != length) { response["received"] = received; response["ok"] = false; response["error"] = "short I2C read"; }
    return;
  }

  if (!strcmp(op, "spi.transfer")) {
    JsonVariantConst bytes = request["data"];
    if (!validByteArray(bytes, MAX_TRANSFER)) { setError(response, "invalid SPI payload"); return; }
    uint32_t clockHz = request["clock_hz"] | 1000000UL;
    int mode = request["mode"] | 0;
    const char *bitOrder = request["bit_order"] | "msb";
    if (clockHz < 10000 || clockHz > 12000000 || mode < 0 || mode > 3 ||
        (strcmp(bitOrder, "msb") && strcmp(bitOrder, "lsb"))) {
      setError(response, "invalid SPI settings"); return;
    }
    int csPin = request["cs_pin"] | -1;
    if (request.containsKey("cs_pin") && (csPin < 0 || csPin > 255)) {
      setError(response, "invalid chip-select pin"); return;
    }
    if (csPin >= 0) { pinMode(csPin, OUTPUT); digitalWrite(csPin, HIGH); }
    SPI.beginTransaction(SPISettings(clockHz, !strcmp(bitOrder, "msb") ? MSBFIRST : LSBFIRST, mode));
    if (csPin >= 0) digitalWrite(csPin, LOW);
    JsonArray data = response["data"].to<JsonArray>();
    for (JsonVariantConst byte : bytes.as<JsonArrayConst>()) data.add(SPI.transfer(byte.as<uint8_t>()));
    if (csPin >= 0) digitalWrite(csPin, HIGH);
    SPI.endTransaction();
    return;
  }

  setError(response, "unknown operation");
}

static void processLine(Stream &input, Stream &output) {
  String line = input.readStringUntil('\n');
  line.trim();
  if (line.length() == 0) return;
  StaticJsonDocument<2048> request;
  StaticJsonDocument<3072> response;
  if (line.length() > MAX_LINE) {
    setError(response, "request too large");
    serializeJson(response, output);
    output.write('\n');
    return;
  }
  DeserializationError parseError = deserializeJson(request, line);
  if (parseError) {
    setError(response, "invalid JSON request");
  } else {
    handleRequest(request, response);
  }
  serializeJson(response, output);
  output.write('\n');
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  Wire.begin();
  Wire.setClock(100000);
  SPI.begin();
  Serial.println("{\"ok\":true,\"event\":\"ready\",\"board\":\"LinkIt 7697\"}");

  if (strlen(PROTOGATE_WIFI_SSID) > 0) {
    LWiFi.begin();
    if (LWiFi.connect((char *)PROTOGATE_WIFI_SSID, (char *)PROTOGATE_WIFI_PASSWORD) == 0) {
      wifiServer.begin();
      Serial.println("{\"ok\":true,\"event\":\"wifi_ready\",\"port\":4242}");
    } else {
      Serial.println("{\"ok\":false,\"event\":\"wifi_failed\"}");
    }
  }
}

void loop() {
  if (Serial.available()) processLine(Serial, Serial);
  if (!wifiClient || !wifiClient.connected()) wifiClient = wifiServer.available();
  if (wifiClient && wifiClient.available()) processLine(wifiClient, wifiClient);
}
