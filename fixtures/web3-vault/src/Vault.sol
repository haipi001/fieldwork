// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract Vault {
    mapping(address => uint256) public balanceOf;
    uint256 public totalAssets;

    function deposit() external payable {
        balanceOf[msg.sender] += msg.value;
        totalAssets += msg.value;
    }

    function withdraw(uint256 amount) external {
        require(balanceOf[msg.sender] >= amount, "insufficient");
        balanceOf[msg.sender] -= amount;
        totalAssets -= amount;
        payable(msg.sender).transfer(amount);
    }
}
