// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "src/Vault.sol";

interface Vm {
    function deal(address who, uint256 newBalance) external;
}

contract VaultInvariantTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    function testFuzz_DepositAccounting(uint96 amount) public {
        if (amount == 0) amount = 1;
        Vault vault = new Vault();
        vm.deal(address(this), amount);
        vault.deposit{value: amount}();
        require(vault.totalAssets() == amount, "asset accounting drift");
        require(vault.balanceOf(address(this)) == amount, "share accounting drift");
    }
}
