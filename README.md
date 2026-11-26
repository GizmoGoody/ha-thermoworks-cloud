# ThermoWorks Cloud for Home Assistant
![GitHub branch check runs](https://img.shields.io/github/check-runs/a2hill/ha-thermoworks-cloud/main)
[![License](https://img.shields.io/github/license/a2hill/ha-thermoworks-cloud)](https://raw.githubusercontent.com/a2hill/ha-thermoworks-cloud/refs/heads/main/LICENSE)

## About
This integration allows [Home Assistant](https://www.home-assistant.io/) to pull data (temperature, battery, signal strength) from ThermoWorks Cloud connected devices.

### Supported Devices
See [Discussions - Device Interoperability](https://github.com/a2hill/ha-thermoworks-cloud/discussions/6)

## Installation

### Marketplace
Since Home Assistant 2026.11, HACS is built in as the Marketplace. Go to **Settings → Marketplace**, open the **⋮** menu, choose **Add from link**, enter `a2hill/ha-thermoworks-cloud` and choose the type **Integration**. The Marketplace needs a GitHub connection.

### HACS (Home Assistant 2026.10 or older)
If you have [HACS](https://hacs.xyz/) installed in your Home Assistant instance

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=a2hill&repository=ha-thermoworks-cloud&category=integration)

### Manual Installation
Copy the [custom_components/thermoworks_cloud](custom_components/) folder into the `config/custom_components` folder of your Home Assistant instance

## Usage
1. With this custom component installed into your HA instance, you will now see the ThermoWorks Cloud integration available as an integration that can be added (settings > Devices & services > Add Integration)
1. For ThermoWorks Cloud, create an API key under **Settings → Account Settings → Integrations** in the ThermoWorks Cloud web app, then enter it during setup. Existing entries will request a key through reauthentication. ETI Cloud continues to use email and password.
1. After adding the integration, you will be required to set the [scan interval](#controlling-the-scan-interval) which will tell HA how often to request new data from ThermoWorks. This can be changed later.  
    * The default is 1,800 seconds (30 minutes), however this may be too slow for real-time applications like grilling with RFX

### Controlling the Scan Interval
It may be desirable to change the scan interval based on the activity that the devices is being used for. There are two ways to change it:

1. **Manually**: Settings > Devices & services > ThermoWorks Cloud > **Configure**, then set **Scan interval (seconds)**.
1. **Programatically**: the integration creates a `number` entity named **Scan interval**, you can control this entity from automations and scripts (see [example](#example-poll-faster-during-a-cook)).

#### Tips
* Polling more often than your device sends readings to the cloud does not give you fresher data. How often a device sends depends on the device:
    * An RFX MEAT probe sends when its temperature changes by its **Temperature-Based Transmission Interval** (1.0°, 1.5° or 3.0°, set per probe in the ThermoWorks app). Its **Transmit Interval** sensor always shows 60 seconds and does not reflect this setting.
    * For other devices, the **Transmit Interval** sensor gives a rough idea of how often the device sends readings.
* Each poll uses part of your API key's daily budget. See [ThermoWorks API limits](#thermoworks-api-limits) before you choose a short interval.
*  Scan interval is a configuration entity, so Home Assistant does not add it to auto-generated dashboards. You can still add it to a dashboard card yourself.

#### Example: poll faster during a cook
This automation polls every 15 seconds while an `input_boolean.cook_mode` helper is on and goes back to every 60 seconds when it is turned off. On the free plan, 60 seconds the rest of the day leaves room for a cook of several hours at 15 seconds (see [Choosing a scan interval](#choosing-a-scan-interval)). Each change of the scan interval reloads the integration, which costs one request. You can use any trigger that suits your setup instead of the helper, such as a probe temperature, a device's **Last Seen** time, or whether your gateway is on the network.

```yaml
alias: ThermoWorks - fast polling during a cook
triggers:
  - trigger: state
    entity_id: input_boolean.cook_mode
actions:
  - action: number.set_value
    target:
      entity_id: number.thermoworks_cloud_scan_interval
    data:
      value: "{{ 15 if is_state('input_boolean.cook_mode', 'on') else 60 }}"
mode: restart
```

## ThermoWorks API limits
These apply to ThermoWorks Cloud entries, which use an API key. ETI Cloud uses email and password and its limits are not known.

| Plan | Requests a minute | Requests a day |
|---|---|---|
| Free | 4 | 3,000 |
| Paid plans with API access | 60 | 50,000 |

* The limits apply **per API key**.
* The daily count resets at **midnight UTC**, for example 7 PM Eastern Standard Time (8 PM during daylight saving time). The ThermoWorks Cloud web app shows each key's **Requests today**.
* ThermoWorks caches its answers for 15 seconds, so polling more often than every 15 seconds cannot return new data. This is why the shortest scan interval is 15 seconds.
* When a key runs out of requests, ThermoWorks answers with "rate limited" and says how long to wait. The integration stops polling for that long, and its entities are unavailable until it can poll again. After the daily limit, that can be until midnight UTC.

### What uses requests
* **Each poll** is one request. When nothing changed, ThermoWorks answers "not modified", which still counts toward both the per-minute and the daily limit.
* **Adding the integration** sends one request to check the key.
* **Every reload** of the integration, including each change of the scan interval and each Home Assistant restart, makes the next poll a full request.
* **Every diagnostics download** sends one request.
* **Anything else that uses the same key**, such as another Home Assistant instance or a script, uses the same budget.

### Use one key per integration
ThermoWorks recommends a separate API key for each integration. Then nothing else uses this integration's budget, and you can revoke one key without affecting anything else.

### Choosing a scan interval
On the free plan (3,000 requests a day):

| Scan interval | Requests an hour | Budget lasts | Requests a day |
|---|---|---|---|
| 15 s | 240 | 12.5 h | 5,760 |
| 20 s | 180 | 16.7 h | 4,320 |
| 25 s | 144 | 20.8 h | 3,456 |
| 29 s | 124 | 24.2 h | 2,980 |
| 30 s | 120 | 25 h | 2,880 |

* 30 seconds or longer lasts all day.
* After using a shorter interval for part of the day, the shortest interval that lasts until the reset is: seconds until midnight UTC ÷ requests remaining, rounded up, and at least 15.
* Paid plans with API access can poll every 15 seconds all day.

## Readings and timestamps
* ThermoWorks keeps each channel's last reading however old it is, and so does this integration. A temperature stays at its last value while a probe is quiet, docked or out of range.
* A device's **Last Seen** sensor is when the device last contacted ThermoWorks Cloud, not when a reading was taken.
* Each temperature sensor's `last_telemetry_saved` attribute is when its reading was taken.

### RFX MEAT probes
* A probe sends when its temperature changes (see [Tips](#tips)). A probe at a steady temperature and a probe on its dock look the same: both keep their last reading.
* The **Battery** sensor does not show a charging icon for a docked probe, because ThermoWorks reports docked probes as discharging.
## Also
To pull data, this integration uses [python-thermoworks-cloud](https://github.com/a2hill/python-thermoworks-cloud)
