import Foundation
import Security

// Secrets travel only through anonymous pipes, never argv or temporary files.
let input = FileHandle.standardInput.readDataToEndOfFile()
guard let object = try? JSONSerialization.jsonObject(with: input) as? [String: Any],
      let account = object["account"] as? String, !account.isEmpty,
      let operation = object["operation"] as? String else { exit(2) }
var query: [String: Any] = [
    kSecClass as String: kSecClassGenericPassword,
    kSecAttrService as String: "fieldwork-session",
    kSecAttrAccount as String: account
]
var status: OSStatus = errSecParam
switch operation {
case "store":
    guard let headers = object["headers"] as? [String: String], !headers.isEmpty,
          let payload = try? JSONSerialization.data(withJSONObject: ["headers": headers]) else { exit(2) }
    status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: payload] as CFDictionary)
    if status == errSecItemNotFound {
        query[kSecValueData as String] = payload
        status = SecItemAdd(query as CFDictionary, nil)
    }
case "read":
    query[kSecReturnData as String] = true
    query[kSecMatchLimit as String] = kSecMatchLimitOne
    var value: CFTypeRef?
    status = SecItemCopyMatching(query as CFDictionary, &value)
    if status == errSecSuccess, let data = value as? Data {
        FileHandle.standardOutput.write(data)
    }
case "delete":
    status = SecItemDelete(query as CFDictionary)
default:
    exit(2)
}
if status == errSecItemNotFound { exit(44) }
if status != errSecSuccess {
    FileHandle.standardError.write(Data("keychain_status=\(status)\n".utf8))
    exit(1)
}
