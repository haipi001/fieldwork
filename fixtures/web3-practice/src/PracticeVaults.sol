// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

// Deliberately vulnerable local fixtures. Never deploy with real assets.
contract AccountingPractice {
    mapping(address => uint256) public balances;
    uint256 public totalAssets;
    bool immutable fixedVersion;
    constructor(bool fixed_) { fixedVersion = fixed_; }
    function deposit() external payable { balances[msg.sender] += msg.value; totalAssets += msg.value; }
    function withdraw(uint256 amount) external {
        require(balances[msg.sender] >= amount);
        balances[msg.sender] -= amount;
        if (fixedVersion) totalAssets -= amount;
        (bool ok,) = msg.sender.call{value: amount}(""); require(ok);
    }
}

contract AuthorityPractice {
    address public immutable owner;
    bool immutable fixedVersion;
    constructor(bool fixed_) { owner = msg.sender; fixedVersion = fixed_; }
    receive() external payable {}
    function withdraw() external {
        if (fixedVersion) require(msg.sender == owner, "owner only");
        (bool ok,) = msg.sender.call{value: address(this).balance}(""); require(ok);
    }
}

// Native-asset share model, illustrating ERC-4626-style rounding, not an ERC-4626 implementation.
contract SharePractice {
    mapping(address => uint256) public shares;
    uint256 public totalShares;
    bool immutable fixedVersion;
    constructor(bool fixed_) { fixedVersion = fixed_; }
    receive() external payable {}
    function deposit(uint256 minimumShares) external payable {
        uint256 issued = totalShares == 0 ? msg.value : msg.value * totalShares / (address(this).balance - msg.value);
        if (fixedVersion) require(issued > 0 && issued >= minimumShares, "slippage");
        shares[msg.sender] += issued; totalShares += issued;
    }
    function redeem() external {
        uint256 amount = shares[msg.sender] * address(this).balance / totalShares;
        totalShares -= shares[msg.sender]; shares[msg.sender] = 0;
        (bool ok,) = msg.sender.call{value: amount}(""); require(ok);
    }
}
