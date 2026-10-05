# iPhone OS 1.0 DisplayOrder.plist editor for the ramdisk's BSD awk.
# Accept only the known XML schema; print nothing if parsing fails. The caller
# stages the result and replaces the original only after a successful exit.
BEGIN {
    state = "header"
    count = split(pins, pin, ",")
    for (i = 1; i <= count; i++) {
        if (pin[i] !~ /^[A-Za-z0-9_.-]+$/ || pinned[pin[i]]++) bad = 1
    }
}

function reject() { bad = 1; exit 2 }
function value(s, tag) {
    if (s !~ ("^[A-Za-z0-9_.-]+</" tag ">$")) reject()
    sub("</" tag ">$", "", s)
    return s
}
function entry(id) {
    print "\t\t<dict><key>displayIdentifier</key><string>" id "</string></dict>"
}

function consume(token) {
    gsub(/^[ \t\r\n]+|[ \t\r\n]+$/, "", token)
    if (token == "") return

    if (state == "header") {
        if (token ~ /^<\?xml / || token ~ /^<!DOCTYPE plist /) return
        if (token != "<plist version=\"1.0\">") reject()
        state = "root"
    } else if (state == "root") {
        if (token != "<dict>") reject()
        state = "section-key"
    } else if (state == "section-key") {
        if (token == "</dict>") state = "plist-end"
        else if (token == "<key>") state = "section-name"
        else reject()
    } else if (state == "section-name") {
        section = value(token, "key")
        if (section != "buttonBar" && section != "iconList" && section != "special") reject()
        if (sections[section]++) reject()
        state = "array-start"
    } else if (state == "array-start") {
        if (token == "<array/>") state = "section-key"
        else if (token == "<array>") state = "entry-start"
        else reject()
    } else if (state == "entry-start") {
        if (token == "</array>") state = "section-key"
        else if (token == "<dict>") state = "entry-key"
        else reject()
    } else if (state == "entry-key") {
        if (token != "<key>") reject()
        state = "entry-name"
    } else if (state == "entry-name") {
        if (token != "displayIdentifier</key>") reject()
        state = "string-start"
    } else if (state == "string-start") {
        if (token != "<string>") reject()
        state = "identifier"
    } else if (state == "identifier") {
        id = value(token, "string")
        if (seen[id]++) reject()
        ids[section, ++n[section]] = id
        if (section == "buttonBar") docked[id] = 1
        state = "entry-end"
    } else if (state == "entry-end") {
        if (token != "</dict>") reject()
        state = "entry-start"
    } else if (state == "plist-end") {
        if (token != "</plist>") reject()
        state = "done"
    } else reject()
}

{
    bytes += length($0) + 1
    if (bytes > 65536 || bad) reject()
    buffer = buffer $0 "\n"
    while ((at = index(buffer, ">")) > 0) {
        token = substr(buffer, 1, at)
        buffer = substr(buffer, at + 1)
        consume(token)
    }
}

END {
    if (buffer !~ /^[ \t\r\n]*$/ || bad || state != "done" || !count || !sections["buttonBar"] ||
        !sections["iconList"] || !sections["special"]) exit 2

    # Keep the dock intact. Existing docked launchers already remain visible.
    for (i = 1; i <= count; i++) if (!docked[pin[i]]) icons[++ni] = pin[i]
    for (i = 1; i <= n["iconList"]; i++) {
        id = ids["iconList", i]
        if (!pinned[id]) icons[++ni] = id
    }
    for (i = 1; i <= n["special"]; i++) {
        id = ids["special", i]
        if (!pinned[id]) hidden[++nh] = id
    }
    # A stock 1.0 home screen has 16 main slots. If a custom list is already
    # full, move its last third-party icons to special; Launcher can open them.
    while (ni > 16) {
        for (i = ni; i > 0; i--) if (icons[i] !~ /^com\.apple\./ && !pinned[icons[i]]) break
        if (!i) exit 2
        hidden[++nh] = icons[i]
        for (j = i; j < ni; j++) icons[j] = icons[j + 1]
        ni--
    }
    print "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
    print "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">"
    print "<plist version=\"1.0\"><dict>"
    print "\t<key>buttonBar</key><array>"
    for (i = 1; i <= n["buttonBar"]; i++) entry(ids["buttonBar", i])
    print "\t</array>\n\t<key>iconList</key><array>"
    for (i = 1; i <= ni; i++) entry(icons[i])
    print "\t</array>\n\t<key>special</key><array>"
    for (i = 1; i <= nh; i++) entry(hidden[i])
    print "\t</array>\n</dict></plist>"
    # Old BSD awk only warns on fclose failure; check the buffered write now.
    if (fflush("/dev/stdout") != 0) exit 2
}
