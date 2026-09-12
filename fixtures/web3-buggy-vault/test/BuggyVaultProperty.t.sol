// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "src/BuggyVault.sol";

interface Vm {
    function deal(address who, uint256 newBalance) external;
}

contract BuggyVaultPropertyTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    function testFuzz_TotalAssetsReturnsToZero(uint96 amount) public {
        if (amount == 0) amount = 1;
        BuggyVault vault = new BuggyVault();
        vm.deal(address(this), amount);
        vault.deposit{value: amount}();
        vault.withdraw(amount);
        require(vault.totalAssets() == 0, "totalAssets drift after full withdrawal");
    }

    receive() external payable {}
}
