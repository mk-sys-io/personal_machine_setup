#!/bin/bash
state=$(nmcli -t -f DEVICE,TYPE,STATE device 2>/dev/null | grep -E "^[^:]+:wifi:" | head -1)
if [[ -n "$state" ]]; then
    if echo "$state" | grep -q ":connected$"; then
        essid=$(nmcli -t -f ACTIVE,SSID device wifi list 2>/dev/null | grep "^yes:" | cut -d: -f2)
        sig=$(nmcli -t -f ACTIVE,SIGNAL device wifi list 2>/dev/null | grep "^yes:" | cut -d: -f2)
        echo "{\"text\": \"WIFI $essid ($sig%)\", \"class\": \"on\"}"
    else
        echo "{\"text\": \"WIFI ?\", \"class\": \"off\"}"
    fi
else
    eth=$(nmcli -t -f DEVICE,TYPE,STATE device 2>/dev/null | grep -E "^[^:]+:ethernet:" | head -1)
    if [[ -n "$eth" ]] && echo "$eth" | grep -q ":connected$"; then
        ip=$(nmcli -t -f IP4.ADDRESS device show 2>/dev/null | head -1 | cut -d: -f2 | cut -d/ -f1)
        echo "{\"text\": \"ETH ${ip:-?}\", \"class\": \"on\"}"
    else
        echo "{\"text\": \"Offline\", \"class\": \"off\"}"
    fi
fi
