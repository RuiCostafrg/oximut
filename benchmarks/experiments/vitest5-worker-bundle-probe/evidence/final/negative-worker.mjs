import { createRequire, syncBuiltinESMExports } from "node:module";
import { runInThisContext } from "node:vm";
import { s } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/spy.DDMXghXv.js";
import { a, d, r, s as s$1 } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/index.w_oBZ9N8.js";
import { a as a$1, e, l } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/init.CO4AB7HZ.js";
import { c, g, n } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/module-dependencies._2Eei3FO.js";
import { Traces } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/traces.js";
import { V } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/rpc.Uhkwr2NM.js";
import { c as c$1, s as s$2 } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/index.Bi0NXYPf.js";
import { performance as performance$1 } from "node:perf_hooks";
import { a as a$2, s as s$3 } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/coverage.9iqXE2gN.js";
import { g as g$1, i, v } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/index.CZzC1gGg.js";
import { c as c$2 } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/inspector.CvyFGlXm.js";
import { f, p } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/index.D0heQG4v.js";
import { $ } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/tinyrainbow.Do8I1XRG.js";
import { a as a$3, f as f$1, i as i$1, s as s$4, t } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/display.BlTmubjj.js";
import { A, B, K, e as e$1, f as f$2, g as g$2, t as t$1, u, v as v$1, w, x, y, z } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/pathe.M-eThtNZ.zq3TM4NT.js";
import { a as a$4, b, d as d$1, p as p$1 } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/source-map.DakdQc29.js";
import { calculateSuiteHash, createFileTask, createTaskName, hasFailed, interpretTaskModes } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/task-utils.js";
import timers from "node:timers";
import timersPromises from "node:timers/promises";
import util from "node:util";
import { b as b$1, s as s$5 } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/setup-common.CqAQCXzx.js";
import { a as a$5, g as g$3, p as p$2, r as r$1 } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/utils.DYj33du9.js";
import { w as w$1 } from "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/init-forks.BaDXQSD8.js";
import "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/chai/index.js";
import "node:async_hooks";
import "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/coverage.jGCktcGP.js";
import "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vite/dist/node/module-runner.js";
import "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/index.DmDMHCg8.js";
import "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/plugins.DPQHVT0p.js";
import "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/rawSnapshot.DqSiqIEx.js";
import "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/offset.Dy-5Fdfn.js";
import "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/tinybench/dist/index.js";
import "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/expect-type/dist/index.js";
import "node:fs";
import "node:url";
import "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/modules.BJuCwlRJ.js";
import "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/path.js";
import "node:path";
import "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/module-evaluator.js";
import "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/@vitest/mocker/dist/index.js";
import "file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/@vitest/mocker/dist/redirect.js";
import "node:console";
// node_modules/vitest/dist/chunks/run.CjdgFZiW.js
const RealDate = Date;
function random(seed) {
	const x = Math.sin(seed++) * 1e4;
	return x - Math.floor(x);
}
function shuffle(array, seed = RealDate.now()) {
	let length = array.length;
	while (length) {
		const index = Math.floor(random(seed) * length--);
		const previous = array[length];
		array[length] = array[index];
		array[index] = previous;
		++seed;
	}
	return array;
}
/**
* Diff Match and Patch
* Copyright 2018 The diff-match-patch Authors.
* https://github.com/google/diff-match-patch
*
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
*   http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
*/
/**
* @fileoverview Computes the difference between two texts to create a patch.
* Applies the patch onto another text, allowing for errors.
* @author fraser@google.com (Neil Fraser)
*/
/**
* CHANGES by pedrottimark to diff_match_patch_uncompressed.ts file:
*
* 1. Delete anything not needed to use diff_cleanupSemantic method
* 2. Convert from prototype properties to var declarations
* 3. Convert Diff to class from constructor and prototype
* 4. Add type annotations for arguments and return values
* 5. Add exports
*/
/**
* The data structure representing a diff is an array of tuples:
* [[DIFF_DELETE, 'Hello'], [DIFF_INSERT, 'Goodbye'], [DIFF_EQUAL, ' world.']]
* which means: delete 'Hello', add 'Goodbye' and keep ' world.'
*/
const DIFF_DELETE = -1;
const DIFF_INSERT = 1;
const DIFF_EQUAL = 0;
/**
* Class representing one diff tuple.
* Attempts to look like a two-element array (which is what this used to be).
* @param {number} op Operation, one of: DIFF_DELETE, DIFF_INSERT, DIFF_EQUAL.
* @param {string} text Text to be deleted, inserted, or retained.
* @constructor
*/
var Diff = class {
	0;
	1;
	constructor(op, text) {
		this[0] = op;
		this[1] = text;
	}
};
/**
* Determine the common prefix of two strings.
* @param {string} text1 First string.
* @param {string} text2 Second string.
* @return {number} The number of characters common to the start of each
*     string.
*/
function diff_commonPrefix(text1, text2) {
	if (!text1 || !text2 || text1.charAt(0) !== text2.charAt(0)) return 0;
	let pointermin = 0;
	let pointermax = Math.min(text1.length, text2.length);
	let pointermid = pointermax;
	let pointerstart = 0;
	while (pointermin < pointermid) {
		if (text1.substring(pointerstart, pointermid) === text2.substring(pointerstart, pointermid)) {
			pointermin = pointermid;
			pointerstart = pointermin;
		} else pointermax = pointermid;
		pointermid = Math.floor((pointermax - pointermin) / 2 + pointermin);
	}
	return pointermid;
}
/**
* Determine the common suffix of two strings.
* @param {string} text1 First string.
* @param {string} text2 Second string.
* @return {number} The number of characters common to the end of each string.
*/
function diff_commonSuffix(text1, text2) {
	if (!text1 || !text2 || text1.charAt(text1.length - 1) !== text2.charAt(text2.length - 1)) return 0;
	let pointermin = 0;
	let pointermax = Math.min(text1.length, text2.length);
	let pointermid = pointermax;
	let pointerend = 0;
	while (pointermin < pointermid) {
		if (text1.substring(text1.length - pointermid, text1.length - pointerend) === text2.substring(text2.length - pointermid, text2.length - pointerend)) {
			pointermin = pointermid;
			pointerend = pointermin;
		} else pointermax = pointermid;
		pointermid = Math.floor((pointermax - pointermin) / 2 + pointermin);
	}
	return pointermid;
}
/**
* Determine if the suffix of one string is the prefix of another.
* @param {string} text1 First string.
* @param {string} text2 Second string.
* @return {number} The number of characters common to the end of the first
*     string and the start of the second string.
* @private
*/
function diff_commonOverlap_(text1, text2) {
	const text1_length = text1.length;
	const text2_length = text2.length;
	if (text1_length === 0 || text2_length === 0) return 0;
	if (text1_length > text2_length) text1 = text1.substring(text1_length - text2_length);
	else if (text1_length < text2_length) text2 = text2.substring(0, text1_length);
	const text_length = Math.min(text1_length, text2_length);
	if (text1 === text2) return text_length;
	let best = 0;
	let length = 1;
	while (true) {
		const pattern = text1.substring(text_length - length);
		const found = text2.indexOf(pattern);
		if (found === -1) return best;
		length += found;
		if (found === 0 || text1.substring(text_length - length) === text2.substring(0, length)) {
			best = length;
			length++;
		}
	}
}
/**
* Reduce the number of edits by eliminating semantically trivial equalities.
* @param {!Array.<!diff_match_patch.Diff>} diffs Array of diff tuples.
*/
function diff_cleanupSemantic(diffs) {
	let changes = false;
	const equalities = [];
	let equalitiesLength = 0;
	/** @type {?string} */
	let lastEquality = null;
	let pointer = 0;
	let length_insertions1 = 0;
	let length_deletions1 = 0;
	let length_insertions2 = 0;
	let length_deletions2 = 0;
	while (pointer < diffs.length) {
		if (diffs[pointer][0] === DIFF_EQUAL) {
			equalities[equalitiesLength++] = pointer;
			length_insertions1 = length_insertions2;
			length_deletions1 = length_deletions2;
			length_insertions2 = 0;
			length_deletions2 = 0;
			lastEquality = diffs[pointer][1];
		} else {
			if (diffs[pointer][0] === DIFF_INSERT) length_insertions2 += diffs[pointer][1].length;
			else length_deletions2 += diffs[pointer][1].length;
			if (lastEquality && lastEquality.length <= Math.max(length_insertions1, length_deletions1) && lastEquality.length <= Math.max(length_insertions2, length_deletions2)) {
				diffs.splice(equalities[equalitiesLength - 1], 0, new Diff(DIFF_DELETE, lastEquality));
				diffs[equalities[equalitiesLength - 1] + 1][0] = DIFF_INSERT;
				equalitiesLength--;
				equalitiesLength--;
				pointer = equalitiesLength > 0 ? equalities[equalitiesLength - 1] : -1;
				length_insertions1 = 0;
				length_deletions1 = 0;
				length_insertions2 = 0;
				length_deletions2 = 0;
				lastEquality = null;
				changes = true;
			}
		}
		pointer++;
	}
	if (changes) diff_cleanupMerge(diffs);
	diff_cleanupSemanticLossless(diffs);
	pointer = 1;
	while (pointer < diffs.length) {
		if (diffs[pointer - 1][0] === DIFF_DELETE && diffs[pointer][0] === DIFF_INSERT) {
			const deletion = diffs[pointer - 1][1];
			const insertion = diffs[pointer][1];
			const overlap_length1 = diff_commonOverlap_(deletion, insertion);
			const overlap_length2 = diff_commonOverlap_(insertion, deletion);
			if (overlap_length1 >= overlap_length2) {
				if (overlap_length1 >= deletion.length / 2 || overlap_length1 >= insertion.length / 2) {
					diffs.splice(pointer, 0, new Diff(DIFF_EQUAL, insertion.substring(0, overlap_length1)));
					diffs[pointer - 1][1] = deletion.substring(0, deletion.length - overlap_length1);
					diffs[pointer + 1][1] = insertion.substring(overlap_length1);
					pointer++;
				}
			} else if (overlap_length2 >= deletion.length / 2 || overlap_length2 >= insertion.length / 2) {
				diffs.splice(pointer, 0, new Diff(DIFF_EQUAL, deletion.substring(0, overlap_length2)));
				diffs[pointer - 1][0] = DIFF_INSERT;
				diffs[pointer - 1][1] = insertion.substring(0, insertion.length - overlap_length2);
				diffs[pointer + 1][0] = DIFF_DELETE;
				diffs[pointer + 1][1] = deletion.substring(overlap_length2);
				pointer++;
			}
			pointer++;
		}
		pointer++;
	}
}
const nonAlphaNumericRegex_ = /[^a-z0-9]/i;
const whitespaceRegex_ = /\s/;
const linebreakRegex_ = /[\r\n]/;
const blanklineEndRegex_ = /\n\r?\n$/;
const blanklineStartRegex_ = /^\r?\n\r?\n/;
/**
* Look for single edits surrounded on both sides by equalities
* which can be shifted sideways to align the edit to a word boundary.
* e.g: The c<ins>at c</ins>ame. -> The <ins>cat </ins>came.
* @param {!Array.<!diff_match_patch.Diff>} diffs Array of diff tuples.
*/
function diff_cleanupSemanticLossless(diffs) {
	let pointer = 1;
	while (pointer < diffs.length - 1) {
		if (diffs[pointer - 1][0] === DIFF_EQUAL && diffs[pointer + 1][0] === DIFF_EQUAL) {
			let equality1 = diffs[pointer - 1][1];
			let edit = diffs[pointer][1];
			let equality2 = diffs[pointer + 1][1];
			const commonOffset = diff_commonSuffix(equality1, edit);
			if (commonOffset) {
				const commonString = edit.substring(edit.length - commonOffset);
				equality1 = equality1.substring(0, equality1.length - commonOffset);
				edit = commonString + edit.substring(0, edit.length - commonOffset);
				equality2 = commonString + equality2;
			}
			let bestEquality1 = equality1;
			let bestEdit = edit;
			let bestEquality2 = equality2;
			let bestScore = diff_cleanupSemanticScore_(equality1, edit) + diff_cleanupSemanticScore_(edit, equality2);
			while (edit.charAt(0) === equality2.charAt(0)) {
				equality1 += edit.charAt(0);
				edit = edit.substring(1) + equality2.charAt(0);
				equality2 = equality2.substring(1);
				const score = diff_cleanupSemanticScore_(equality1, edit) + diff_cleanupSemanticScore_(edit, equality2);
				if (score >= bestScore) {
					bestScore = score;
					bestEquality1 = equality1;
					bestEdit = edit;
					bestEquality2 = equality2;
				}
			}
			if (diffs[pointer - 1][1] !== bestEquality1) {
				if (bestEquality1) diffs[pointer - 1][1] = bestEquality1;
				else {
					diffs.splice(pointer - 1, 1);
					pointer--;
				}
				diffs[pointer][1] = bestEdit;
				if (bestEquality2) diffs[pointer + 1][1] = bestEquality2;
				else {
					diffs.splice(pointer + 1, 1);
					pointer--;
				}
			}
		}
		pointer++;
	}
}
/**
* Reorder and merge like edit sections.  Merge equalities.
* Any edit section can move as long as it doesn't cross an equality.
* @param {!Array.<!diff_match_patch.Diff>} diffs Array of diff tuples.
*/
function diff_cleanupMerge(diffs) {
	diffs.push(new Diff(DIFF_EQUAL, ""));
	let pointer = 0;
	let count_delete = 0;
	let count_insert = 0;
	let text_delete = "";
	let text_insert = "";
	let commonlength;
	while (pointer < diffs.length) switch (diffs[pointer][0]) {
		case DIFF_INSERT:
			count_insert++;
			text_insert += diffs[pointer][1];
			pointer++;
			break;
		case DIFF_DELETE:
			count_delete++;
			text_delete += diffs[pointer][1];
			pointer++;
			break;
		case DIFF_EQUAL:
			if (count_delete + count_insert > 1) {
				if (count_delete !== 0 && count_insert !== 0) {
					commonlength = diff_commonPrefix(text_insert, text_delete);
					if (commonlength !== 0) {
						if (pointer - count_delete - count_insert > 0 && diffs[pointer - count_delete - count_insert - 1][0] === DIFF_EQUAL) diffs[pointer - count_delete - count_insert - 1][1] += text_insert.substring(0, commonlength);
						else {
							diffs.splice(0, 0, new Diff(DIFF_EQUAL, text_insert.substring(0, commonlength)));
							pointer++;
						}
						text_insert = text_insert.substring(commonlength);
						text_delete = text_delete.substring(commonlength);
					}
					commonlength = diff_commonSuffix(text_insert, text_delete);
					if (commonlength !== 0) {
						diffs[pointer][1] = text_insert.substring(text_insert.length - commonlength) + diffs[pointer][1];
						text_insert = text_insert.substring(0, text_insert.length - commonlength);
						text_delete = text_delete.substring(0, text_delete.length - commonlength);
					}
				}
				pointer -= count_delete + count_insert;
				diffs.splice(pointer, count_delete + count_insert);
				if (text_delete.length) {
					diffs.splice(pointer, 0, new Diff(DIFF_DELETE, text_delete));
					pointer++;
				}
				if (text_insert.length) {
					diffs.splice(pointer, 0, new Diff(DIFF_INSERT, text_insert));
					pointer++;
				}
				pointer++;
			} else if (pointer !== 0 && diffs[pointer - 1][0] === DIFF_EQUAL) {
				diffs[pointer - 1][1] += diffs[pointer][1];
				diffs.splice(pointer, 1);
			} else pointer++;
			count_insert = 0;
			count_delete = 0;
			text_delete = "";
			text_insert = "";
	}
	if (diffs.at(-1)?.[1] === "") diffs.pop();
	let changes = false;
	pointer = 1;
	while (pointer < diffs.length - 1) {
		if (diffs[pointer - 1][0] === DIFF_EQUAL && diffs[pointer + 1][0] === DIFF_EQUAL) {
			if (diffs[pointer][1].substring(diffs[pointer][1].length - diffs[pointer - 1][1].length) === diffs[pointer - 1][1]) {
				diffs[pointer][1] = diffs[pointer - 1][1] + diffs[pointer][1].substring(0, diffs[pointer][1].length - diffs[pointer - 1][1].length);
				diffs[pointer + 1][1] = diffs[pointer - 1][1] + diffs[pointer + 1][1];
				diffs.splice(pointer - 1, 1);
				changes = true;
			} else if (diffs[pointer][1].substring(0, diffs[pointer + 1][1].length) === diffs[pointer + 1][1]) {
				diffs[pointer - 1][1] += diffs[pointer + 1][1];
				diffs[pointer][1] = diffs[pointer][1].substring(diffs[pointer + 1][1].length) + diffs[pointer + 1][1];
				diffs.splice(pointer + 1, 1);
				changes = true;
			}
		}
		pointer++;
	}
	if (changes) diff_cleanupMerge(diffs);
}
/**
* Given two strings, compute a score representing whether the internal
* boundary falls on logical boundaries.
* Scores range from 6 (best) to 0 (worst).
* Closure, but does not reference any external variables.
* @param {string} one First string.
* @param {string} two Second string.
* @return {number} The score.
* @private
*/
function diff_cleanupSemanticScore_(one, two) {
	if (!one || !two) return 6;
	const char1 = one.charAt(one.length - 1);
	const char2 = two.charAt(0);
	const nonAlphaNumeric1 = char1.match(nonAlphaNumericRegex_);
	const nonAlphaNumeric2 = char2.match(nonAlphaNumericRegex_);
	const whitespace1 = nonAlphaNumeric1 && char1.match(whitespaceRegex_);
	const whitespace2 = nonAlphaNumeric2 && char2.match(whitespaceRegex_);
	const lineBreak1 = whitespace1 && char1.match(linebreakRegex_);
	const lineBreak2 = whitespace2 && char2.match(linebreakRegex_);
	const blankLine1 = lineBreak1 && one.match(blanklineEndRegex_);
	const blankLine2 = lineBreak2 && two.match(blanklineStartRegex_);
	if (blankLine1 || blankLine2) return 5;
	else if (lineBreak1 || lineBreak2) return 4;
	else if (nonAlphaNumeric1 && !whitespace1 && whitespace2) return 3;
	else if (whitespace1 || whitespace2) return 2;
	else if (nonAlphaNumeric1 || nonAlphaNumeric2) return 1;
	return 0;
}
/**
* Copyright (c) Meta Platforms, Inc. and affiliates.
*
* This source code is licensed under the MIT license found in the
* LICENSE file in the root directory of this source tree.
*/
const NO_DIFF_MESSAGE = "Compared values have no visual difference.";
const SIMILAR_MESSAGE = "Compared values serialize to the same structure.\nPrinting internal object structure without calling `toJSON` instead.";
function formatTrailingSpaces(line, trailingSpaceFormatter) {
	return line.replace(/\s+$/, (match) => trailingSpaceFormatter(match));
}
function printDiffLine(line, isFirstOrLast, color, indicator, trailingSpaceFormatter, emptyFirstOrLastLinePlaceholder) {
	return line.length !== 0 ? color(`${indicator} ${formatTrailingSpaces(line, trailingSpaceFormatter)}`) : indicator !== " " ? color(indicator) : isFirstOrLast && emptyFirstOrLastLinePlaceholder.length !== 0 ? color(`${indicator} ${emptyFirstOrLastLinePlaceholder}`) : "";
}
function printDeleteLine(line, isFirstOrLast, { aColor, aIndicator, changeLineTrailingSpaceColor, emptyFirstOrLastLinePlaceholder }) {
	return printDiffLine(line, isFirstOrLast, aColor, aIndicator, changeLineTrailingSpaceColor, emptyFirstOrLastLinePlaceholder);
}
function printInsertLine(line, isFirstOrLast, { bColor, bIndicator, changeLineTrailingSpaceColor, emptyFirstOrLastLinePlaceholder }) {
	return printDiffLine(line, isFirstOrLast, bColor, bIndicator, changeLineTrailingSpaceColor, emptyFirstOrLastLinePlaceholder);
}
function printCommonLine(line, isFirstOrLast, { commonColor, commonIndicator, commonLineTrailingSpaceColor, emptyFirstOrLastLinePlaceholder }) {
	return printDiffLine(line, isFirstOrLast, commonColor, commonIndicator, commonLineTrailingSpaceColor, emptyFirstOrLastLinePlaceholder);
}
function createPatchMark(aStart, aEnd, bStart, bEnd, { patchColor }) {
	return patchColor(`@@ -${aStart + 1},${aEnd - aStart} +${bStart + 1},${bEnd - bStart} @@`);
}
function joinAlignedDiffsNoExpand(diffs, options) {
	const iLength = diffs.length;
	const nContextLines = options.contextLines;
	const nContextLines2 = nContextLines + nContextLines;
	let jLength = iLength;
	let hasExcessAtStartOrEnd = false;
	let nExcessesBetweenChanges = 0;
	let i = 0;
	while (i !== iLength) {
		const iStart = i;
		while (i !== iLength && diffs[i][0] === DIFF_EQUAL) i += 1;
		if (iStart !== i) {
			if (iStart === 0) {
				if (i > nContextLines) {
					jLength -= i - nContextLines;
					hasExcessAtStartOrEnd = true;
				}
			} else if (i === iLength) {
				const n = i - iStart;
				if (n > nContextLines) {
					jLength -= n - nContextLines;
					hasExcessAtStartOrEnd = true;
				}
			} else {
				const n = i - iStart;
				if (n > nContextLines2) {
					jLength -= n - nContextLines2;
					nExcessesBetweenChanges += 1;
				}
			}
		}
		while (i !== iLength && diffs[i][0] !== DIFF_EQUAL) i += 1;
	}
	const hasPatch = nExcessesBetweenChanges !== 0 || hasExcessAtStartOrEnd;
	if (nExcessesBetweenChanges !== 0) jLength += nExcessesBetweenChanges + 1;
	else if (hasExcessAtStartOrEnd) jLength += 1;
	const jLast = jLength - 1;
	const lines = [];
	let jPatchMark = 0;
	if (hasPatch) lines.push("");
	let aStart = 0;
	let bStart = 0;
	let aEnd = 0;
	let bEnd = 0;
	const pushCommonLine = (line) => {
		const j = lines.length;
		lines.push(printCommonLine(line, j === 0 || j === jLast, options));
		aEnd += 1;
		bEnd += 1;
	};
	const pushDeleteLine = (line) => {
		const j = lines.length;
		lines.push(printDeleteLine(line, j === 0 || j === jLast, options));
		aEnd += 1;
	};
	const pushInsertLine = (line) => {
		const j = lines.length;
		lines.push(printInsertLine(line, j === 0 || j === jLast, options));
		bEnd += 1;
	};
	i = 0;
	while (i !== iLength) {
		let iStart = i;
		while (i !== iLength && diffs[i][0] === DIFF_EQUAL) i += 1;
		if (iStart !== i) {
			if (iStart === 0) {
				if (i > nContextLines) {
					iStart = i - nContextLines;
					aStart = iStart;
					bStart = iStart;
					aEnd = aStart;
					bEnd = bStart;
				}
				for (let iCommon = iStart; iCommon !== i; iCommon += 1) pushCommonLine(diffs[iCommon][1]);
			} else if (i === iLength) {
				const iEnd = i - iStart > nContextLines ? iStart + nContextLines : i;
				for (let iCommon = iStart; iCommon !== iEnd; iCommon += 1) pushCommonLine(diffs[iCommon][1]);
			} else {
				const nCommon = i - iStart;
				if (nCommon > nContextLines2) {
					const iEnd = iStart + nContextLines;
					for (let iCommon = iStart; iCommon !== iEnd; iCommon += 1) pushCommonLine(diffs[iCommon][1]);
					lines[jPatchMark] = createPatchMark(aStart, aEnd, bStart, bEnd, options);
					jPatchMark = lines.length;
					lines.push("");
					const nOmit = nCommon - nContextLines2;
					aStart = aEnd + nOmit;
					bStart = bEnd + nOmit;
					aEnd = aStart;
					bEnd = bStart;
					for (let iCommon = i - nContextLines; iCommon !== i; iCommon += 1) pushCommonLine(diffs[iCommon][1]);
				} else for (let iCommon = iStart; iCommon !== i; iCommon += 1) pushCommonLine(diffs[iCommon][1]);
			}
		}
		while (i !== iLength && diffs[i][0] === DIFF_DELETE) {
			pushDeleteLine(diffs[i][1]);
			i += 1;
		}
		while (i !== iLength && diffs[i][0] === DIFF_INSERT) {
			pushInsertLine(diffs[i][1]);
			i += 1;
		}
	}
	if (hasPatch) lines[jPatchMark] = createPatchMark(aStart, aEnd, bStart, bEnd, options);
	return lines.join("\n");
}
function joinAlignedDiffsExpand(diffs, options) {
	return diffs.map((diff, i, diffs) => {
		const line = diff[1];
		const isFirstOrLast = i === 0 || i === diffs.length - 1;
		switch (diff[0]) {
			case DIFF_DELETE: return printDeleteLine(line, isFirstOrLast, options);
			case DIFF_INSERT: return printInsertLine(line, isFirstOrLast, options);
			default: return printCommonLine(line, isFirstOrLast, options);
		}
	}).join("\n");
}
const noColor = (string) => string;
const DIFF_CONTEXT_DEFAULT = 5;
const DIFF_TRUNCATE_THRESHOLD_DEFAULT = 0;
function getDefaultOptions() {
	return {
		aAnnotation: "Expected",
		aColor: $.green,
		aIndicator: "-",
		bAnnotation: "Received",
		bColor: $.red,
		bIndicator: "+",
		changeColor: $.inverse,
		changeLineTrailingSpaceColor: noColor,
		commonColor: $.dim,
		commonIndicator: " ",
		commonLineTrailingSpaceColor: noColor,
		compareKeys: void 0,
		contextLines: DIFF_CONTEXT_DEFAULT,
		emptyFirstOrLastLinePlaceholder: "",
		expand: false,
		includeChangeCounts: false,
		omitAnnotationLines: false,
		patchColor: $.yellow,
		printBasicPrototype: false,
		truncateThreshold: DIFF_TRUNCATE_THRESHOLD_DEFAULT,
		truncateAnnotation: "... Diff result is truncated",
		truncateAnnotationColor: noColor
	};
}
function getCompareKeys(compareKeys) {
	return compareKeys && typeof compareKeys === "function" ? compareKeys : void 0;
}
function getContextLines(contextLines) {
	return typeof contextLines === "number" && Number.isSafeInteger(contextLines) && contextLines >= 0 ? contextLines : DIFF_CONTEXT_DEFAULT;
}
function normalizeDiffOptions(options = {}) {
	return {
		...getDefaultOptions(),
		...options,
		compareKeys: getCompareKeys(options.compareKeys),
		contextLines: getContextLines(options.contextLines)
	};
}
function isEmptyString(lines) {
	return lines.length === 1 && lines[0].length === 0;
}
function countChanges(diffs) {
	let a = 0;
	let b = 0;
	diffs.forEach((diff) => {
		switch (diff[0]) {
			case DIFF_DELETE:
				a += 1;
				break;
			case DIFF_INSERT: b += 1;
		}
	});
	return {
		a,
		b
	};
}
function printAnnotation({ aAnnotation, aColor, aIndicator, bAnnotation, bColor, bIndicator, includeChangeCounts, omitAnnotationLines }, changeCounts) {
	if (omitAnnotationLines) return "";
	let aRest = "";
	let bRest = "";
	if (includeChangeCounts) {
		const aCount = String(changeCounts.a);
		const bCount = String(changeCounts.b);
		const baAnnotationLengthDiff = bAnnotation.length - aAnnotation.length;
		const aAnnotationPadding = " ".repeat(Math.max(0, baAnnotationLengthDiff));
		const bAnnotationPadding = " ".repeat(Math.max(0, -baAnnotationLengthDiff));
		const baCountLengthDiff = bCount.length - aCount.length;
		const aCountPadding = " ".repeat(Math.max(0, baCountLengthDiff));
		const bCountPadding = " ".repeat(Math.max(0, -baCountLengthDiff));
		aRest = `${aAnnotationPadding}  ${aIndicator} ${aCountPadding}${aCount}`;
		bRest = `${bAnnotationPadding}  ${bIndicator} ${bCountPadding}${bCount}`;
	}
	const a = `${aIndicator} ${aAnnotation}${aRest}`;
	const b = `${bIndicator} ${bAnnotation}${bRest}`;
	return `${aColor(a)}\n${bColor(b)}\n\n`;
}
function printDiffLines(diffs, truncated, options) {
	return printAnnotation(options, countChanges(diffs)) + (options.expand ? joinAlignedDiffsExpand(diffs, options) : joinAlignedDiffsNoExpand(diffs, options)) + (truncated ? options.truncateAnnotationColor(`\n${options.truncateAnnotation}`) : "");
}
function diffLinesUnified(aLines, bLines, options) {
	const normalizedOptions = normalizeDiffOptions(options);
	const [diffs, truncated] = diffLinesRaw(isEmptyString(aLines) ? [] : aLines, isEmptyString(bLines) ? [] : bLines, normalizedOptions);
	return printDiffLines(diffs, truncated, normalizedOptions);
}
function diffLinesUnified2(aLinesDisplay, bLinesDisplay, aLinesCompare, bLinesCompare, options) {
	if (isEmptyString(aLinesDisplay) && isEmptyString(aLinesCompare)) {
		aLinesDisplay = [];
		aLinesCompare = [];
	}
	if (isEmptyString(bLinesDisplay) && isEmptyString(bLinesCompare)) {
		bLinesDisplay = [];
		bLinesCompare = [];
	}
	if (aLinesDisplay.length !== aLinesCompare.length || bLinesDisplay.length !== bLinesCompare.length) return diffLinesUnified(aLinesDisplay, bLinesDisplay, options);
	const [diffs, truncated] = diffLinesRaw(aLinesCompare, bLinesCompare, options);
	let aIndex = 0;
	let bIndex = 0;
	diffs.forEach((diff) => {
		switch (diff[0]) {
			case DIFF_DELETE:
				diff[1] = aLinesDisplay[aIndex];
				aIndex += 1;
				break;
			case DIFF_INSERT:
				diff[1] = bLinesDisplay[bIndex];
				bIndex += 1;
				break;
			default:
				diff[1] = bLinesDisplay[bIndex];
				aIndex += 1;
				bIndex += 1;
		}
	});
	return printDiffLines(diffs, truncated, normalizeDiffOptions(options));
}
function diffLinesRaw(aLines, bLines, options) {
	const truncate = options?.truncateThreshold ?? false;
	const truncateThreshold = Math.max(Math.floor(options?.truncateThreshold ?? 0), 0);
	const aLength = truncate ? Math.min(aLines.length, truncateThreshold) : aLines.length;
	const bLength = truncate ? Math.min(bLines.length, truncateThreshold) : bLines.length;
	const truncated = aLength !== aLines.length || bLength !== bLines.length;
	const isCommon = (aIndex, bIndex) => aLines[aIndex] === bLines[bIndex];
	const diffs = [];
	let aIndex = 0;
	let bIndex = 0;
	const foundSubsequence = (nCommon, aCommon, bCommon) => {
		for (; aIndex !== aCommon; aIndex += 1) diffs.push(new Diff(DIFF_DELETE, aLines[aIndex]));
		for (; bIndex !== bCommon; bIndex += 1) diffs.push(new Diff(DIFF_INSERT, bLines[bIndex]));
		for (; nCommon !== 0; nCommon -= 1, aIndex += 1, bIndex += 1) diffs.push(new Diff(DIFF_EQUAL, bLines[bIndex]));
	};
	d$1(aLength, bLength, isCommon, foundSubsequence);
	for (; aIndex !== aLength; aIndex += 1) diffs.push(new Diff(DIFF_DELETE, aLines[aIndex]));
	for (; bIndex !== bLength; bIndex += 1) diffs.push(new Diff(DIFF_INSERT, bLines[bIndex]));
	return [diffs, truncated];
}
function getType(value) {
	if (value === void 0) return "undefined";
	else if (value === null) return "null";
	else if (Array.isArray(value)) return "array";
	else if (typeof value === "boolean") return "boolean";
	else if (typeof value === "function") return "function";
	else if (typeof value === "number") return "number";
	else if (typeof value === "string") return "string";
	else if (typeof value === "bigint") return "bigint";
	else if (typeof value === "object") {
		if (value != null) {
			if (value.constructor === RegExp) return "regexp";
			else if (value.constructor === Map) return "map";
			else if (value.constructor === Set) return "set";
			else if (value.constructor === Date) return "date";
		}
		return "object";
	} else if (typeof value === "symbol") return "symbol";
	throw new Error(`value of unknown type: ${value}`);
}
function getNewLineSymbol(string) {
	return string.includes("\r\n") ? "\r\n" : "\n";
}
function diffStrings(a, b, options) {
	const truncate = options?.truncateThreshold ?? false;
	const truncateThreshold = Math.max(Math.floor(options?.truncateThreshold ?? 0), 0);
	let aLength = a.length;
	let bLength = b.length;
	if (truncate) {
		const aMultipleLines = a.includes("\n");
		const bMultipleLines = b.includes("\n");
		const aNewLineSymbol = getNewLineSymbol(a);
		const bNewLineSymbol = getNewLineSymbol(b);
		const _a = aMultipleLines ? `${a.split(aNewLineSymbol, truncateThreshold).join(aNewLineSymbol)}\n` : a;
		const _b = bMultipleLines ? `${b.split(bNewLineSymbol, truncateThreshold).join(bNewLineSymbol)}\n` : b;
		aLength = _a.length;
		bLength = _b.length;
	}
	const truncated = aLength !== a.length || bLength !== b.length;
	const isCommon = (aIndex, bIndex) => a[aIndex] === b[bIndex];
	let aIndex = 0;
	let bIndex = 0;
	const diffs = [];
	const foundSubsequence = (nCommon, aCommon, bCommon) => {
		if (aIndex !== aCommon) diffs.push(new Diff(DIFF_DELETE, a.slice(aIndex, aCommon)));
		if (bIndex !== bCommon) diffs.push(new Diff(DIFF_INSERT, b.slice(bIndex, bCommon)));
		aIndex = aCommon + nCommon;
		bIndex = bCommon + nCommon;
		diffs.push(new Diff(DIFF_EQUAL, b.slice(bCommon, bIndex)));
	};
	d$1(aLength, bLength, isCommon, foundSubsequence);
	if (aIndex !== aLength) diffs.push(new Diff(DIFF_DELETE, a.slice(aIndex)));
	if (bIndex !== bLength) diffs.push(new Diff(DIFF_INSERT, b.slice(bIndex)));
	return [diffs, truncated];
}
function concatenateRelevantDiffs(op, diffs, changeColor) {
	return diffs.reduce((reduced, diff) => reduced + (diff[0] === DIFF_EQUAL ? diff[1] : diff[0] === op && diff[1].length !== 0 ? changeColor(diff[1]) : ""), "");
}
var ChangeBuffer = class {
	op;
	line;
	lines;
	changeColor;
	constructor(op, changeColor) {
		this.op = op;
		this.line = [];
		this.lines = [];
		this.changeColor = changeColor;
	}
	pushSubstring(substring) {
		this.pushDiff(new Diff(this.op, substring));
	}
	pushLine() {
		this.lines.push(this.line.length !== 1 ? new Diff(this.op, concatenateRelevantDiffs(this.op, this.line, this.changeColor)) : this.line[0][0] === this.op ? this.line[0] : new Diff(this.op, this.line[0][1]));
		this.line.length = 0;
	}
	isLineEmpty() {
		return this.line.length === 0;
	}
	pushDiff(diff) {
		this.line.push(diff);
	}
	align(diff) {
		const string = diff[1];
		if (string.includes("\n")) {
			const substrings = string.split("\n");
			const iLast = substrings.length - 1;
			substrings.forEach((substring, i) => {
				if (i < iLast) {
					this.pushSubstring(substring);
					this.pushLine();
				} else if (substring.length !== 0) this.pushSubstring(substring);
			});
		} else this.pushDiff(diff);
	}
	moveLinesTo(lines) {
		if (!this.isLineEmpty()) this.pushLine();
		lines.push(...this.lines);
		this.lines.length = 0;
	}
};
var CommonBuffer = class {
	deleteBuffer;
	insertBuffer;
	lines;
	constructor(deleteBuffer, insertBuffer) {
		this.deleteBuffer = deleteBuffer;
		this.insertBuffer = insertBuffer;
		this.lines = [];
	}
	pushDiffCommonLine(diff) {
		this.lines.push(diff);
	}
	pushDiffChangeLines(diff) {
		const isDiffEmpty = diff[1].length === 0;
		if (!isDiffEmpty || this.deleteBuffer.isLineEmpty()) this.deleteBuffer.pushDiff(diff);
		if (!isDiffEmpty || this.insertBuffer.isLineEmpty()) this.insertBuffer.pushDiff(diff);
	}
	flushChangeLines() {
		this.deleteBuffer.moveLinesTo(this.lines);
		this.insertBuffer.moveLinesTo(this.lines);
	}
	align(diff) {
		const op = diff[0];
		const string = diff[1];
		if (string.includes("\n")) {
			const substrings = string.split("\n");
			const iLast = substrings.length - 1;
			substrings.forEach((substring, i) => {
				if (i === 0) {
					const subdiff = new Diff(op, substring);
					if (this.deleteBuffer.isLineEmpty() && this.insertBuffer.isLineEmpty()) {
						this.flushChangeLines();
						this.pushDiffCommonLine(subdiff);
					} else {
						this.pushDiffChangeLines(subdiff);
						this.flushChangeLines();
					}
				} else if (i < iLast) this.pushDiffCommonLine(new Diff(op, substring));
				else if (substring.length !== 0) this.pushDiffChangeLines(new Diff(op, substring));
			});
		} else this.pushDiffChangeLines(diff);
	}
	getLines() {
		this.flushChangeLines();
		return this.lines;
	}
};
function getAlignedDiffs(diffs, changeColor) {
	const deleteBuffer = new ChangeBuffer(DIFF_DELETE, changeColor);
	const insertBuffer = new ChangeBuffer(DIFF_INSERT, changeColor);
	const commonBuffer = new CommonBuffer(deleteBuffer, insertBuffer);
	diffs.forEach((diff) => {
		switch (diff[0]) {
			case DIFF_DELETE:
				deleteBuffer.align(diff);
				break;
			case DIFF_INSERT:
				insertBuffer.align(diff);
				break;
			default: commonBuffer.align(diff);
		}
	});
	return commonBuffer.getLines();
}
function hasCommonDiff(diffs, isMultiline) {
	if (isMultiline) {
		const iLast = diffs.length - 1;
		return diffs.some((diff, i) => diff[0] === DIFF_EQUAL && (i !== iLast || diff[1] !== "\n"));
	}
	return diffs.some((diff) => diff[0] === DIFF_EQUAL);
}
function diffStringsUnified(a, b, options) {
	if (a !== b && a.length !== 0 && b.length !== 0) {
		const isMultiline = a.includes("\n") || b.includes("\n");
		const [diffs, truncated] = diffStringsRaw(isMultiline ? `${a}\n` : a, isMultiline ? `${b}\n` : b, true, options);
		if (hasCommonDiff(diffs, isMultiline)) {
			const optionsNormalized = normalizeDiffOptions(options);
			return printDiffLines(getAlignedDiffs(diffs, optionsNormalized.changeColor), truncated, optionsNormalized);
		}
	}
	return diffLinesUnified(a.split("\n"), b.split("\n"), options);
}
function diffStringsRaw(a, b, cleanup, options) {
	const [diffs, truncated] = diffStrings(a, b, options);
	diff_cleanupSemantic(diffs);
	return [diffs, truncated];
}
function getCommonMessage(message, options) {
	const { commonColor } = normalizeDiffOptions(options);
	return commonColor(message);
}
const { AsymmetricMatcher, DOMCollection, DOMElement, Immutable, ReactElement, ReactTestComponent } = p;
const PLUGINS = [
	ReactTestComponent,
	ReactElement,
	DOMElement,
	DOMCollection,
	Immutable,
	AsymmetricMatcher,
	p.Error
];
const FORMAT_OPTIONS = {
	maxDepth: 20,
	plugins: PLUGINS
};
const FALLBACK_FORMAT_OPTIONS = {
	callToJSON: false,
	maxDepth: 8,
	plugins: PLUGINS
};
const DEFAULT_MEMORIZE = (_, v) => v;
/**
* @param a Expected value
* @param b Received value
* @param options Diff options
* @returns {string | null} a string diff
*/
function diff(a, b, options, memorize = DEFAULT_MEMORIZE) {
	if (Object.is(a, b)) return "";
	const aType = getType(a);
	let expectedType = aType;
	let omitDifference = false;
	if (aType === "object" && typeof a.asymmetricMatch === "function") {
		if (a.$$typeof !== Symbol.for("jest.asymmetricMatcher")) return;
		if (typeof a.getExpectedType !== "function") return;
		expectedType = a.getExpectedType();
		omitDifference = expectedType === "string";
	}
	if (expectedType !== getType(b)) {
		const { aAnnotation, aColor, aIndicator, bAnnotation, bColor, bIndicator } = normalizeDiffOptions(options);
		const formatOptions = getFormatOptions(FALLBACK_FORMAT_OPTIONS, options);
		let aDisplay = f(a, formatOptions);
		let bDisplay = f(b, formatOptions);
		const MAX_LENGTH = 1e5;
		function truncate(s) {
			return s.length <= MAX_LENGTH ? s : `${s.slice(0, MAX_LENGTH)}...`;
		}
		aDisplay = memorize("expected", truncate(aDisplay));
		bDisplay = memorize("actual", truncate(bDisplay));
		return `${`${aColor(`${aIndicator} ${aAnnotation}:`)}\n${aDisplay}`}\n\n${`${bColor(`${bIndicator} ${bAnnotation}:`)}\n${bDisplay}`}`;
	}
	if (omitDifference) return;
	switch (aType) {
		case "string": return diffLinesUnified(a.split("\n"), b.split("\n"), options);
		case "boolean":
		case "number": return comparePrimitive(a, b, options, memorize);
		case "map": return compareObjects(sortMap(a), sortMap(b), options, memorize);
		case "set": return compareObjects(sortSet(a), sortSet(b), options, memorize);
		default: return compareObjects(a, b, options, memorize);
	}
}
function createMemorize(memory) {
	return (pointer, stringifiedValue) => {
		memory[pointer] = stringifiedValue;
		return stringifiedValue;
	};
}
function comparePrimitive(a, b, options, memorize = DEFAULT_MEMORIZE) {
	const aFormat = memorize("expected", f(a, FORMAT_OPTIONS));
	const bFormat = memorize("actual", f(b, FORMAT_OPTIONS));
	return aFormat === bFormat ? "" : diffLinesUnified(aFormat.split("\n"), bFormat.split("\n"), options);
}
function sortMap(map) {
	return new Map(Array.from(map.entries()).sort());
}
function sortSet(set) {
	return new Set(Array.from(set.values()).sort());
}
function compareObjects(a, b, options, memorize = DEFAULT_MEMORIZE) {
	let difference;
	let hasThrown = false;
	try {
		difference = getObjectsDifference(a, b, getFormatOptions(FORMAT_OPTIONS, options), options, memorize);
	} catch {
		hasThrown = true;
	}
	const noDiffMessage = getCommonMessage(NO_DIFF_MESSAGE, options);
	if (difference === void 0 || difference === noDiffMessage) {
		difference = getObjectsDifference(a, b, getFormatOptions(FALLBACK_FORMAT_OPTIONS, options), options, memorize);
		if (difference !== noDiffMessage && !hasThrown) difference = `${getCommonMessage(SIMILAR_MESSAGE, options)}\n\n${difference}`;
	}
	return difference;
}
function getDefaultFormatOptions(options) {
	return getFormatOptions(FORMAT_OPTIONS, options);
}
function getFormatOptions(formatOptions, options) {
	const { compareKeys, printBasicPrototype, maxDepth } = normalizeDiffOptions(options);
	return {
		...formatOptions,
		compareKeys,
		printBasicPrototype,
		maxDepth: maxDepth ?? formatOptions.maxDepth
	};
}
function getObjectsDifference(a, b, formatOptions, options, memorize = DEFAULT_MEMORIZE) {
	const formatOptionsZeroIndent = {
		...formatOptions,
		indent: 0
	};
	const aCompare = f(a, formatOptionsZeroIndent);
	const bCompare = f(b, formatOptionsZeroIndent);
	if (aCompare === bCompare) return getCommonMessage(NO_DIFF_MESSAGE, options);
	else {
		const aDisplay = memorize("expected", f(a, formatOptions));
		const bDisplay = memorize("actual", f(b, formatOptions));
		return diffLinesUnified2(aDisplay.split("\n"), bDisplay.split("\n"), aCompare.split("\n"), bCompare.split("\n"), options);
	}
}
const MAX_DIFF_STRING_LENGTH = 2e4;
function isAsymmetricMatcher(data) {
	return g$2(data) === "Object" && typeof data.asymmetricMatch === "function";
}
function isReplaceable(obj1, obj2) {
	const obj1Type = g$2(obj1);
	return obj1Type === g$2(obj2) && (obj1Type === "Object" || obj1Type === "Array");
}
function printDiffOrStringify(received, expected, options, memory) {
	const { aAnnotation, bAnnotation } = normalizeDiffOptions(options);
	if (typeof expected === "string" && typeof received === "string" && expected.length > 0 && received.length > 0 && expected.length <= MAX_DIFF_STRING_LENGTH && received.length <= MAX_DIFF_STRING_LENGTH && expected !== received) {
		if (expected.includes("\n") || received.includes("\n")) return diffStringsUnified(expected, received, options);
		const [diffs] = diffStringsRaw(expected, received);
		const hasCommonDiff = diffs.some((diff) => diff[0] === DIFF_EQUAL);
		const printLabel = getLabelPrinter(aAnnotation, bAnnotation);
		return `${printLabel(aAnnotation) + printExpected(getCommonAndChangedSubstrings(diffs, DIFF_DELETE, hasCommonDiff))}\n${printLabel(bAnnotation) + printReceived(getCommonAndChangedSubstrings(diffs, DIFF_INSERT, hasCommonDiff))}`;
	}
	const clonedExpected = u(expected, { forceWritable: true });
	const { replacedExpected, replacedActual } = replaceAsymmetricMatcher(u(received, { forceWritable: true }), clonedExpected);
	return diff(replacedExpected, replacedActual, options, memory ? createMemorize(memory) : DEFAULT_MEMORIZE);
}
function replaceAsymmetricMatcher(actual, expected, actualReplaced = /* @__PURE__ */ new WeakSet(), expectedReplaced = /* @__PURE__ */ new WeakSet()) {
	if (actual instanceof Error && expected instanceof Error && typeof actual.cause !== "undefined" && typeof expected.cause === "undefined") {
		delete actual.cause;
		return {
			replacedActual: actual,
			replacedExpected: expected
		};
	}
	if (!isReplaceable(actual, expected)) return {
		replacedActual: actual,
		replacedExpected: expected
	};
	if (actualReplaced.has(actual) || expectedReplaced.has(expected)) return {
		replacedActual: actual,
		replacedExpected: expected
	};
	actualReplaced.add(actual);
	expectedReplaced.add(expected);
	v$1(expected).forEach((key) => {
		const expectedValue = expected[key];
		const actualValue = actual[key];
		if (isAsymmetricMatcher(expectedValue)) {
			if (expectedValue.asymmetricMatch(actualValue)) expected[key] = actualValue;
			else if ("sample" in expectedValue && expectedValue.sample !== void 0 && isReplaceable(actualValue, expectedValue.sample)) {
				const replaced = replaceAsymmetricMatcher(actualValue, expectedValue.sample, actualReplaced, expectedReplaced);
				actual[key] = replaced.replacedActual;
				expected[key] = replaced.replacedExpected;
			}
		} else if (isAsymmetricMatcher(actualValue)) {
			if (actualValue.asymmetricMatch(expectedValue)) actual[key] = expectedValue;
			else if ("sample" in actualValue && actualValue.sample !== void 0 && isReplaceable(actualValue.sample, expectedValue)) {
				const replaced = replaceAsymmetricMatcher(actualValue.sample, expectedValue, actualReplaced, expectedReplaced);
				actual[key] = replaced.replacedActual;
				expected[key] = replaced.replacedExpected;
			}
		} else if (isReplaceable(actualValue, expectedValue)) {
			const replaced = replaceAsymmetricMatcher(actualValue, expectedValue, actualReplaced, expectedReplaced);
			actual[key] = replaced.replacedActual;
			expected[key] = replaced.replacedExpected;
		}
	});
	return {
		replacedActual: actual,
		replacedExpected: expected
	};
}
function getLabelPrinter(...strings) {
	const maxLength = strings.reduce((max, string) => string.length > max ? string.length : max, 0);
	return (string) => `${string}: ${" ".repeat(maxLength - string.length)}`;
}
const SPACE_SYMBOL = "·";
function replaceTrailingSpaces(text) {
	return text.replace(/\s+$/gm, (spaces) => SPACE_SYMBOL.repeat(spaces.length));
}
function printReceived(object) {
	return $.red(replaceTrailingSpaces(s$4(object)));
}
function printExpected(value) {
	return $.green(replaceTrailingSpaces(s$4(value)));
}
function getCommonAndChangedSubstrings(diffs, op, hasCommonDiff) {
	return diffs.reduce((reduced, diff) => reduced + (diff[0] === DIFF_EQUAL ? diff[1] : diff[0] === op ? hasCommonDiff ? $.inverse(diff[1]) : diff[1] : ""), "");
}
function processError(_err, diffOptions, seen = /* @__PURE__ */ new WeakSet()) {
	if (!_err || typeof _err !== "object") return { message: String(_err) };
	const err = _err;
	if (err.showDiff || err.showDiff === void 0 && err.expected !== void 0 && err.actual !== void 0) {
		const options = {
			...diffOptions,
			...err.diffOptions
		};
		err.diff = printDiffOrStringify(err.actual, err.expected, options, err);
		err.expected = prettifyValue(err.expected, options);
		err.actual = prettifyValue(err.actual, options);
	}
	try {
		if (!seen.has(err) && typeof err.cause === "object") {
			seen.add(err);
			err.cause = processError(err.cause, diffOptions, seen);
		}
	} catch {}
	try {
		return b(err);
	} catch (e) {
		return b(/* @__PURE__ */ new Error(`Failed to fully serialize error: ${e?.message}\nInner error message: ${err?.message}`));
	}
}
function prettifyValue(value, options) {
	if (typeof value !== "string") return f(value, getDefaultFormatOptions(options));
	return value;
}
/**
* Return a function for running multiple async operations with limited concurrency.
*/
function limitConcurrency(concurrency = Infinity) {
	let count = 0;
	let head;
	let tail;
	const finish = () => {
		count--;
		if (head) {
			head[0]();
			head = head[1];
			tail = head && tail;
		}
	};
	const acquire = () => {
		let released = false;
		const release = () => {
			if (!released) {
				released = true;
				finish();
			}
		};
		if (count++ < concurrency) return release;
		return new Promise((resolve) => {
			if (tail) tail = tail[1] = [() => resolve(release)];
			else head = tail = [() => resolve(release)];
		});
	};
	const limiterFn = (func, ...args) => {
		function run(release) {
			try {
				const result = func(...args);
				if (result instanceof Promise) return result.finally(release);
				release();
				return Promise.resolve(result);
			} catch (error) {
				release();
				return Promise.reject(error);
			}
		}
		const release = acquire();
		return release instanceof Promise ? release.then(run) : run(release);
	};
	return Object.assign(limiterFn, { acquire });
}
var PendingError = class extends Error {
	message;
	note;
	code = "VITEST_PENDING";
	taskId;
	constructor(message, task, note) {
		super(message);
		this.message = message;
		this.note = note;
		this.taskId = task.id;
	}
};
var TestRunAbortError = class extends Error {
	name = "TestRunAbortError";
	reason;
	constructor(message, reason) {
		super(message);
		this.reason = reason;
	}
};
var FixtureDependencyError = class extends Error {
	name = "FixtureDependencyError";
};
var FixtureAccessError = class extends Error {
	name = "FixtureAccessError";
};
var FixtureParseError = class extends Error {
	name = "FixtureParseError";
};
var AroundHookSetupError = class extends Error {
	name = "AroundHookSetupError";
};
var AroundHookTeardownError = class extends Error {
	name = "AroundHookTeardownError";
};
var AroundHookMultipleCallsError = class extends Error {
	name = "AroundHookMultipleCallsError";
};
const fnMap = /* @__PURE__ */ new WeakMap();
const testFixtureMap = /* @__PURE__ */ new WeakMap();
const hooksMap = /* @__PURE__ */ new WeakMap();
function setFn(key, fn) {
	fnMap.set(key, fn);
}
function getFn(key) {
	return fnMap.get(key);
}
function setTestFixture(key, fixture) {
	testFixtureMap.set(key, fixture);
}
function getTestFixtures(key) {
	return testFixtureMap.get(key);
}
function setHooks(key, hooks) {
	hooksMap.set(key, hooks);
}
function getHooks(key) {
	return hooksMap.get(key);
}
const FIXTURE_STACK_TRACE_KEY = Symbol.for("VITEST_FIXTURE_STACK_TRACE");
var TestFixtures = class TestFixtures {
	_suiteContexts;
	_overrides = /* @__PURE__ */ new WeakMap();
	_registrations;
	static _definitions = [];
	static _builtinFixtures = [
		"task",
		"signal",
		"onTestFailed",
		"onTestFinished",
		"skip",
		"annotate",
		"bench"
	];
	static _fixtureOptionKeys = [
		"auto",
		"injected",
		"scope"
	];
	static _fixtureScopes = [
		"test",
		"file",
		"worker"
	];
	static _workerContextSuite = { type: "worker" };
	static clearDefinitions() {
		TestFixtures._definitions.length = 0;
	}
	static getWorkerContexts() {
		return TestFixtures._definitions.map((f) => f.getWorkerContext());
	}
	static getFileContexts(file) {
		return TestFixtures._definitions.map((f) => f.getFileContext(file));
	}
	static isFixtureOptions(obj) {
		return f$2(obj) && Object.keys(obj).some((key) => TestFixtures._fixtureOptionKeys.includes(key));
	}
	constructor(registrations) {
		this._registrations = registrations ?? /* @__PURE__ */ new Map();
		this._suiteContexts = /* @__PURE__ */ new WeakMap();
		TestFixtures._definitions.push(this);
	}
	extend(runner, userFixtures) {
		const { suite } = getCurrentSuite();
		const isTopLevel = !suite || suite.file === suite;
		const registrations = this.parseUserFixtures(runner, userFixtures, isTopLevel);
		return new TestFixtures(registrations);
	}
	get(suite) {
		let currentSuite = suite;
		while (currentSuite) {
			const overrides = this._overrides.get(currentSuite);
			if (overrides) return overrides;
			if (currentSuite === currentSuite.file) break;
			currentSuite = currentSuite.suite || currentSuite.file;
		}
		return this._registrations;
	}
	override(runner, userFixtures) {
		const { suite: currentSuite, file } = getCurrentSuite();
		const suite = currentSuite || file;
		const isTopLevel = !currentSuite || currentSuite.file === currentSuite;
		const suiteRegistrations = new Map(this.get(suite));
		const registrations = this.parseUserFixtures(runner, userFixtures, isTopLevel, suiteRegistrations);
		if (isTopLevel) this._registrations = registrations;
		else this._overrides.set(suite, registrations);
	}
	getFileContext(file) {
		if (!this._suiteContexts.has(file)) this._suiteContexts.set(file, Object.create(null));
		return this._suiteContexts.get(file);
	}
	getWorkerContext() {
		if (!this._suiteContexts.has(TestFixtures._workerContextSuite)) this._suiteContexts.set(TestFixtures._workerContextSuite, Object.create(null));
		return this._suiteContexts.get(TestFixtures._workerContextSuite);
	}
	parseUserFixtures(runner, userFixtures, supportNonTest, registrations = new Map(this._registrations)) {
		const errors = [];
		Object.entries(userFixtures).forEach(([name, fn]) => {
			let options;
			let value;
			let _options;
			if (Array.isArray(fn) && fn.length >= 2 && TestFixtures.isFixtureOptions(fn[1])) {
				_options = fn[1];
				options = {
					auto: _options.auto ?? false,
					scope: _options.scope ?? "test",
					injected: _options.injected ?? false
				};
				value = options.injected ? runner.injectValue?.(name) ?? fn[0] : fn[0];
			} else value = fn;
			const parent = registrations.get(name);
			if (parent && options) {
				if (parent.scope !== options.scope) errors.push(new FixtureDependencyError(`The "${name}" fixture was already registered with a "${options.scope}" scope.`));
				if (parent.auto !== options.auto) errors.push(new FixtureDependencyError(`The "${name}" fixture was already registered as { auto: ${options.auto} }.`));
			} else if (parent) options = {
				auto: parent.auto,
				scope: parent.scope,
				injected: parent.injected
			};
			else if (!options) options = {
				auto: false,
				injected: false,
				scope: "test"
			};
			if (options.scope && !TestFixtures._fixtureScopes.includes(options.scope)) errors.push(new FixtureDependencyError(`The "${name}" fixture has unknown scope "${options.scope}".`));
			if (!supportNonTest && options.scope !== "test") errors.push(new FixtureDependencyError(`The "${name}" fixture cannot be defined with a ${options.scope} scope${!_options?.scope && parent?.scope ? " (inherited from the base fixture)" : ""} inside the describe block. Define it at the top level of the file instead.`));
			const deps = isFixtureFunction(value) ? getUsedProps(value) : /* @__PURE__ */ new Set();
			const item = {
				name,
				value,
				auto: options.auto ?? false,
				injected: options.injected ?? false,
				scope: options.scope ?? "test",
				deps,
				parent
			};
			if (isFixtureFunction(value)) Object.assign(value, { [FIXTURE_STACK_TRACE_KEY]: /* @__PURE__ */ new Error("STACK_TRACE_ERROR") });
			registrations.set(name, item);
			if (item.scope === "worker" && (runner.pool === "vmThreads" || runner.pool === "vmForks")) item.scope = "file";
		});
		for (const fixture of registrations.values()) for (const depName of fixture.deps) {
			if (TestFixtures._builtinFixtures.includes(depName)) continue;
			const dep = registrations.get(depName);
			if (!dep) {
				errors.push(new FixtureDependencyError(`The "${fixture.name}" fixture depends on unknown fixture "${depName}".`));
				continue;
			}
			if (depName === fixture.name && !fixture.parent) {
				errors.push(new FixtureDependencyError(`The "${fixture.name}" fixture depends on itself, but does not have a base implementation.`));
				continue;
			}
			if (TestFixtures._fixtureScopes.indexOf(fixture.scope) > TestFixtures._fixtureScopes.indexOf(dep.scope)) {
				errors.push(new FixtureDependencyError(`The ${fixture.scope} "${fixture.name}" fixture cannot depend on a ${dep.scope} fixture "${dep.name}".`));
				continue;
			}
		}
		if (errors.length === 1) throw errors[0];
		else if (errors.length > 1) throw new AggregateError(errors, "Cannot resolve user fixtures. See errors for more information.");
		return registrations;
	}
};
const cleanupFnArrayMap = /* @__PURE__ */ new WeakMap();
async function callFixtureCleanup(context) {
	const cleanupFnArray = cleanupFnArrayMap.get(context) ?? [];
	for (const cleanup of cleanupFnArray.reverse()) await cleanup();
	cleanupFnArrayMap.delete(context);
}
/**
* Returns the current number of cleanup functions registered for the context.
* This can be used as a checkpoint to later clean up only fixtures added after this point.
*/
function getFixtureCleanupCount(context) {
	return cleanupFnArrayMap.get(context)?.length ?? 0;
}
/**
* Cleans up only fixtures that were added after the given checkpoint index.
* This is used by aroundEach to clean up fixtures created inside runTest()
* while preserving fixtures that were created for aroundEach itself.
*/
async function callFixtureCleanupFrom(context, fromIndex) {
	const cleanupFnArray = cleanupFnArrayMap.get(context);
	if (!cleanupFnArray || cleanupFnArray.length <= fromIndex) return;
	const toCleanup = cleanupFnArray.slice(fromIndex);
	for (const cleanup of toCleanup.reverse()) await cleanup();
	cleanupFnArray.length = fromIndex;
}
const contextHasFixturesCache = /* @__PURE__ */ new WeakMap();
function withFixtures(fn, options) {
	const collector = getCurrentSuite();
	const suite = options?.suite || collector.suite || collector.file;
	return async (hookContext) => {
		const context = hookContext || options?.context;
		if (!context) {
			if (options?.suiteHook) validateSuiteHook(fn, options.suiteHook, options.stackTraceError);
			return fn({});
		}
		const fixtures = options?.fixtures || getTestFixtures(context);
		if (!fixtures) return fn(context);
		const registrations = fixtures.get(suite);
		if (!registrations.size) return fn(context);
		const usedFixtures = [];
		const usedProps = getUsedProps(fn);
		for (const fixture of registrations.values()) if (isAutoFixture(fixture, options) || usedProps.has(fixture.name)) usedFixtures.push(fixture);
		if (!usedFixtures.length) return fn(context);
		if (!cleanupFnArrayMap.has(context)) cleanupFnArrayMap.set(context, []);
		const cleanupFnArray = cleanupFnArrayMap.get(context);
		const pendingFixtures = resolveDeps(usedFixtures, registrations);
		if (!pendingFixtures.length) return fn(context);
		if (options?.suiteHook) {
			const testScopedFixtures = pendingFixtures.filter((f) => f.scope === "test");
			if (testScopedFixtures.length > 0) {
				const fixtureNames = testScopedFixtures.map((f) => `"${f.name}"`).join(", ");
				const error = new FixtureDependencyError(`Test-scoped fixtures cannot be used inside ${options.suiteHook} hook. The following fixtures are test-scoped: ${fixtureNames}. Use { scope: 'file' } or { scope: 'worker' } fixtures instead, or move the logic to ${{
					aroundAll: "aroundEach",
					beforeAll: "beforeEach",
					afterAll: "afterEach"
				}[options.suiteHook]} hook.`);
				if (options.stackTraceError?.stack) error.stack = error.message + options.stackTraceError.stack.replace(options.stackTraceError.message, "");
				throw error;
			}
		}
		if (!contextHasFixturesCache.has(context)) contextHasFixturesCache.set(context, /* @__PURE__ */ new WeakSet());
		const cachedFixtures = contextHasFixturesCache.get(context);
		for (const fixture of pendingFixtures) if (fixture.scope === "test") {
			if (cachedFixtures.has(fixture)) continue;
			cachedFixtures.add(fixture);
			try {
				context[fixture.name] = await resolveTestFixtureValue(fixture, context, cleanupFnArray);
			} catch (error) {
				cachedFixtures.delete(fixture);
				throw error;
			}
			cleanupFnArray.push(() => {
				cachedFixtures.delete(fixture);
			});
		} else {
			const resolvedValue = await resolveScopeFixtureValue(fixtures, suite, fixture);
			context[fixture.name] = resolvedValue;
		}
		return fn(context);
	};
}
function isAutoFixture(fixture, options) {
	if (!fixture.auto) return false;
	if (options?.suiteHook && fixture.scope === "test") return false;
	return true;
}
function isFixtureFunction(value) {
	return typeof value === "function";
}
function resolveTestFixtureValue(fixture, context, cleanupFnArray) {
	if (!isFixtureFunction(fixture.value)) return fixture.value;
	return resolveFixtureFunction(fixture.value, fixture.name, context, cleanupFnArray);
}
const scopedFixturePromiseCache = /* @__PURE__ */ new WeakMap();
async function resolveScopeFixtureValue(fixtures, suite, fixture) {
	const workerContext = fixtures.getWorkerContext();
	const fileContext = fixtures.getFileContext(suite.file);
	const fixtureContext = fixture.scope === "worker" ? workerContext : fileContext;
	if (!isFixtureFunction(fixture.value)) {
		fixtureContext[fixture.name] = fixture.value;
		return fixture.value;
	}
	if (fixture.name in fixtureContext) return fixtureContext[fixture.name];
	if (scopedFixturePromiseCache.has(fixture)) return scopedFixturePromiseCache.get(fixture);
	if (!cleanupFnArrayMap.has(fixtureContext)) cleanupFnArrayMap.set(fixtureContext, []);
	const cleanupFnFileArray = cleanupFnArrayMap.get(fixtureContext);
	const promise = resolveFixtureFunction(fixture.value, fixture.name, fixture.scope === "file" ? {
		...workerContext,
		...fileContext
	} : fixtureContext, cleanupFnFileArray).then((value) => {
		fixtureContext[fixture.name] = value;
		return value;
	}).finally(() => {
		scopedFixturePromiseCache.delete(fixture);
	});
	scopedFixturePromiseCache.set(fixture, promise);
	return promise;
}
async function resolveFixtureFunction(fixtureFn, fixtureName, context, cleanupFnArray) {
	const useFnArgPromise = e$1();
	const stackTraceError = FIXTURE_STACK_TRACE_KEY in fixtureFn && fixtureFn[FIXTURE_STACK_TRACE_KEY] instanceof Error ? fixtureFn[FIXTURE_STACK_TRACE_KEY] : void 0;
	let isUseFnArgResolved = false;
	const fixtureReturn = fixtureFn(context, async (useFnArg) => {
		isUseFnArgResolved = true;
		useFnArgPromise.resolve(useFnArg);
		const useReturnPromise = e$1();
		cleanupFnArray.push(async () => {
			useReturnPromise.resolve();
			await fixtureReturn;
		});
		await useReturnPromise;
	}).then(() => {
		if (!isUseFnArgResolved) {
			const error = /* @__PURE__ */ new Error(`Fixture "${fixtureName}" returned without calling "use". Make sure to call "use" in every code path of the fixture function.`);
			if (stackTraceError?.stack) error.stack = error.message + stackTraceError.stack.replace(stackTraceError.message, "");
			useFnArgPromise.reject(error);
		}
	}).catch((e) => {
		if (!isUseFnArgResolved) {
			useFnArgPromise.reject(e);
			return;
		}
		throw e;
	});
	return useFnArgPromise;
}
function resolveDeps(usedFixtures, registrations, depSet = /* @__PURE__ */ new Set(), pendingFixtures = []) {
	usedFixtures.forEach((fixture) => {
		if (pendingFixtures.includes(fixture)) return;
		if (!isFixtureFunction(fixture.value) || !fixture.deps) {
			pendingFixtures.push(fixture);
			return;
		}
		if (depSet.has(fixture)) {
			if (fixture.parent) fixture = fixture.parent;
			else throw new Error(`Circular fixture dependency detected: ${fixture.name} <- ${[...depSet].reverse().map((d) => d.name).join(" <- ")}`);
		}
		depSet.add(fixture);
		resolveDeps(Array.from(fixture.deps, (n) => n === fixture.name ? fixture.parent : registrations.get(n)).filter((n) => !!n), registrations, depSet, pendingFixtures);
		pendingFixtures.push(fixture);
		depSet.clear();
	});
	return pendingFixtures;
}
function validateSuiteHook(fn, hook, suiteError) {
	const usedProps = getUsedProps(fn, {
		sourceError: suiteError,
		suiteHook: hook
	});
	if (usedProps.size) {
		const error = new FixtureAccessError(`The ${hook} hook uses fixtures "${[...usedProps].join("\", \"")}", but has no access to context. Did you forget to call it as "test.${hook}()" instead of "${hook}()"?\nIf you used internal "suite" task as the first argument previously, access it in the second argument instead. See https://vitest.dev/guide/test-context#suite-level-hooks`);
		if (suiteError) error.stack = suiteError.stack?.replace(suiteError.message, error.message);
		throw error;
	}
}
const kPropsSymbol = Symbol("$vitest:fixture-props");
const kPropNamesSymbol = Symbol("$vitest:fixture-prop-names");
function configureProps(fn, options) {
	Object.defineProperty(fn, kPropsSymbol, {
		value: options,
		enumerable: false
	});
}
function memoProps(fn, props) {
	fn[kPropNamesSymbol] = props;
	return props;
}
function getUsedProps(fn, { sourceError, suiteHook } = {}) {
	if (kPropNamesSymbol in fn) return fn[kPropNamesSymbol];
	const { index: fixturesIndex = 0, original: implementation = fn } = kPropsSymbol in fn ? fn[kPropsSymbol] : {};
	let fnString = w(implementation.toString());
	if (/__async\((?:this|null), (?:null|arguments|\[[_0-9, ]*\]), function\*/.test(fnString)) fnString = fnString.split(/__async\((?:this|null),/)[1];
	const match = fnString.match(/[^(]*\(([^)]*)/);
	if (!match) return memoProps(fn, /* @__PURE__ */ new Set());
	const args = splitByComma(match[1]);
	if (!args.length) return memoProps(fn, /* @__PURE__ */ new Set());
	const fixturesArgument = args[fixturesIndex];
	if (!fixturesArgument) return memoProps(fn, /* @__PURE__ */ new Set());
	if (!(fixturesArgument[0] === "{" && fixturesArgument.endsWith("}"))) {
		const ordinalArgument = x(fixturesIndex + 1);
		const error = new FixtureParseError(`The ${ordinalArgument} argument inside a fixture must use object destructuring pattern, e.g. ({ task } => {}). Instead, received "${fixturesArgument}".${suiteHook ? ` If you used internal "suite" task as the ${ordinalArgument} argument previously, access it in the ${x(fixturesIndex + 2)} argument instead.` : ""}`);
		if (sourceError) error.stack = sourceError.stack?.replace(sourceError.message, error.message);
		throw error;
	}
	const props = splitByComma(fixturesArgument.slice(1, -1).replace(/\s/g, "")).map((prop) => {
		return prop.replace(/:.*|=.*/g, "");
	});
	const last = props.at(-1);
	if (last && last.startsWith("...")) {
		const error = new FixtureParseError(`Rest parameters are not supported in fixtures, received "${last}".`);
		if (sourceError) error.stack = sourceError.stack?.replace(sourceError.message, error.message);
		throw error;
	}
	return memoProps(fn, new Set(props));
}
function splitByComma(s) {
	const result = [];
	const stack = [];
	let start = 0;
	for (let i = 0; i < s.length; i++) if (s[i] === "{" || s[i] === "[") stack.push(s[i] === "{" ? "}" : "]");
	else if (s[i] === stack.at(-1)) stack.pop();
	else if (!stack.length && s[i] === ",") {
		const token = s.substring(start, i).trim();
		if (token) result.push(token);
		start = i + 1;
	}
	const lastToken = s.substring(start).trim();
	if (lastToken) result.push(lastToken);
	return result;
}
let _test;
function setCurrentTest(test) {
	_test = test;
}
function getCurrentTest() {
	return _test;
}
const tests = /* @__PURE__ */ new Set();
function addRunningTest(test) {
	tests.add(test);
	return () => {
		tests.delete(test);
	};
}
function getRunningTests() {
	return tests;
}
const kChainableContext = Symbol("kChainableContext");
function getChainableContext(chainable) {
	return chainable?.[kChainableContext];
}
function createChainable(keys, fn, context) {
	function create(context) {
		const chain = function(...args) {
			return fn.apply(context, args);
		};
		Object.assign(chain, fn);
		Object.defineProperty(chain, kChainableContext, {
			value: {
				withContext: () => chain.bind(context),
				extendContext: (ctx) => create({
					...context,
					...ctx
				}),
				getFixtures: () => context.fixtures,
				setContext: (key, value) => {
					context[key] = value;
				},
				mergeContext: (ctx) => {
					Object.assign(context, ctx);
				}
			},
			enumerable: false
		});
		for (const key of keys) Object.defineProperty(chain, key, { get() {
			return create({
				...context,
				[key]: true
			});
		} });
		return chain;
	}
	const chain = create(context ?? {});
	Object.defineProperty(chain, "fn", {
		value: fn,
		enumerable: false
	});
	return chain;
}
function getDefaultHookTimeout() {
	return getRunner().config.hookTimeout;
}
const CLEANUP_TIMEOUT_KEY = Symbol.for("VITEST_CLEANUP_TIMEOUT");
const CLEANUP_STACK_TRACE_KEY = Symbol.for("VITEST_CLEANUP_STACK_TRACE");
const AROUND_TIMEOUT_KEY = Symbol.for("VITEST_AROUND_TIMEOUT");
const AROUND_STACK_TRACE_KEY = Symbol.for("VITEST_AROUND_STACK_TRACE");
function getBeforeHookCleanupCallback(hook, result, context) {
	if (typeof result === "function") return withTimeout(result, CLEANUP_TIMEOUT_KEY in hook && typeof hook[CLEANUP_TIMEOUT_KEY] === "number" ? hook[CLEANUP_TIMEOUT_KEY] : getDefaultHookTimeout(), true, CLEANUP_STACK_TRACE_KEY in hook && hook[CLEANUP_STACK_TRACE_KEY] instanceof Error ? hook[CLEANUP_STACK_TRACE_KEY] : void 0, (_, error) => {
		if (context) abortContextSignal(context, error);
	});
}
/**
* Registers a callback function to be executed once before all tests within the current suite.
* This hook is useful for scenarios where you need to perform setup operations that are common to all tests in a suite, such as initializing a database connection or setting up a test environment.
*
* **Note:** The `beforeAll` hooks are executed in the order they are defined one after another. You can configure this by changing the `sequence.hooks` option in the config file.
*
* @param {Function} fn - The callback function to be executed before all tests.
* @param {number} [timeout] - Optional timeout in milliseconds for the hook. If not provided, the default hook timeout from the runner's configuration is used.
* @returns {void}
* @example
* ```ts
* // Example of using beforeAll to set up a database connection
* beforeAll(async () => {
*   await database.connect();
* });
* ```
*/
function beforeAll(fn, timeout = getDefaultHookTimeout()) {
	y(fn, "\"beforeAll\" callback", ["function"]);
	const stackTraceError = /* @__PURE__ */ new Error("STACK_TRACE_ERROR");
	const context = getChainableContext(this);
	return getCurrentSuite().on("beforeAll", Object.assign(withTimeout(withSuiteFixtures("beforeAll", fn, context, stackTraceError), timeout, true, stackTraceError), {
		[CLEANUP_TIMEOUT_KEY]: timeout,
		[CLEANUP_STACK_TRACE_KEY]: stackTraceError
	}));
}
/**
* Registers a callback function to be executed once after all tests within the current suite have completed.
* This hook is useful for scenarios where you need to perform cleanup operations after all tests in a suite have run, such as closing database connections or cleaning up temporary files.
*
* **Note:** The `afterAll` hooks are running in reverse order of their registration. You can configure this by changing the `sequence.hooks` option in the config file.
*
* @param {Function} fn - The callback function to be executed after all tests.
* @param {number} [timeout] - Optional timeout in milliseconds for the hook. If not provided, the default hook timeout from the runner's configuration is used.
* @returns {void}
* @example
* ```ts
* // Example of using afterAll to close a database connection
* afterAll(async () => {
*   await database.disconnect();
* });
* ```
*/
function afterAll(fn, timeout) {
	y(fn, "\"afterAll\" callback", ["function"]);
	const context = getChainableContext(this);
	const stackTraceError = /* @__PURE__ */ new Error("STACK_TRACE_ERROR");
	return getCurrentSuite().on("afterAll", withTimeout(withSuiteFixtures("afterAll", fn, context, stackTraceError), timeout ?? getDefaultHookTimeout(), true, stackTraceError));
}
/**
* Registers a callback function to be executed before each test within the current suite.
* This hook is useful for scenarios where you need to reset or reinitialize the test environment before each test runs, such as resetting database states, clearing caches, or reinitializing variables.
*
* **Note:** The `beforeEach` hooks are executed in the order they are defined one after another. You can configure this by changing the `sequence.hooks` option in the config file.
*
* @param {Function} fn - The callback function to be executed before each test. This function receives an `TestContext` parameter if additional test context is needed.
* @param {number} [timeout] - Optional timeout in milliseconds for the hook. If not provided, the default hook timeout from the runner's configuration is used.
* @returns {void}
* @example
* ```ts
* // Example of using beforeEach to reset a database state
* beforeEach(async () => {
*   await database.reset();
* });
* ```
*/
function beforeEach(fn, timeout = getDefaultHookTimeout()) {
	y(fn, "\"beforeEach\" callback", ["function"]);
	const stackTraceError = /* @__PURE__ */ new Error("STACK_TRACE_ERROR");
	const wrapper = (context, suite) => {
		return withFixtures(fn, { suite })(context);
	};
	return getCurrentSuite().on("beforeEach", Object.assign(withTimeout(wrapper, timeout ?? getDefaultHookTimeout(), true, stackTraceError, abortIfTimeout), {
		[CLEANUP_TIMEOUT_KEY]: timeout,
		[CLEANUP_STACK_TRACE_KEY]: stackTraceError
	}));
}
/**
* Registers a callback function to be executed after each test within the current suite has completed.
* This hook is useful for scenarios where you need to clean up or reset the test environment after each test runs, such as deleting temporary files, clearing test-specific database entries, or resetting mocked functions.
*
* **Note:** The `afterEach` hooks are running in reverse order of their registration. You can configure this by changing the `sequence.hooks` option in the config file.
*
* @param {Function} fn - The callback function to be executed after each test. This function receives an `TestContext` parameter if additional test context is needed.
* @param {number} [timeout] - Optional timeout in milliseconds for the hook. If not provided, the default hook timeout from the runner's configuration is used.
* @returns {void}
* @example
* ```ts
* // Example of using afterEach to delete temporary files created during a test
* afterEach(async () => {
*   await fileSystem.deleteTempFiles();
* });
* ```
*/
function afterEach(fn, timeout) {
	y(fn, "\"afterEach\" callback", ["function"]);
	const wrapper = (context, suite) => {
		return withFixtures(fn, { suite })(context);
	};
	return getCurrentSuite().on("afterEach", withTimeout(wrapper, timeout ?? getDefaultHookTimeout(), true, /* @__PURE__ */ new Error("STACK_TRACE_ERROR"), abortIfTimeout));
}
/**
* Registers a callback function that wraps around all tests within the current suite.
* The callback receives a `runSuite` function that must be called to run the suite's tests.
* This hook is useful for scenarios where you need to wrap an entire suite in a context
* (e.g., starting a server, opening a database connection that all tests share).
*
* **Note:** When multiple `aroundAll` hooks are registered, they are nested inside each other.
* The first registered hook is the outermost wrapper.
*
* @param {Function} fn - The callback function that wraps the suite. Must call `runSuite()` to run the tests.
* @param {number} [timeout] - Optional timeout in milliseconds for the hook. If not provided, the default hook timeout from the runner's configuration is used.
* @returns {void}
* @example
* ```ts
* // Example of using aroundAll to wrap suite in a tracing span
* aroundAll(async (runSuite) => {
*   await tracer.trace('test-suite', runSuite);
* });
* ```
* @example
* ```ts
* // Example of using aroundAll with fixtures
* aroundAll(async (runSuite, { db }) => {
*   await db.transaction(() => runSuite());
* });
* ```
*/
function aroundAll(fn, timeout) {
	y(fn, "\"aroundAll\" callback", ["function"]);
	const stackTraceError = /* @__PURE__ */ new Error("STACK_TRACE_ERROR");
	const resolvedTimeout = timeout ?? getDefaultHookTimeout();
	const context = getChainableContext(this);
	return getCurrentSuite().on("aroundAll", Object.assign(withSuiteFixtures("aroundAll", fn, context, stackTraceError, 1), {
		[AROUND_TIMEOUT_KEY]: resolvedTimeout,
		[AROUND_STACK_TRACE_KEY]: stackTraceError
	}));
}
/**
* Registers a callback function that wraps around each test within the current suite.
* The callback receives a `runTest` function that must be called to run the test.
* This hook is useful for scenarios where you need to wrap tests in a context (e.g., database transactions).
*
* **Note:** When multiple `aroundEach` hooks are registered, they are nested inside each other.
* The first registered hook is the outermost wrapper.
*
* @param {Function} fn - The callback function that wraps the test. Must call `runTest()` to run the test.
* @param {number} [timeout] - Optional timeout in milliseconds for the hook. If not provided, the default hook timeout from the runner's configuration is used.
* @returns {void}
* @example
* ```ts
* // Example of using aroundEach to wrap tests in a database transaction
* aroundEach(async (runTest) => {
*   await database.transaction(() => runTest());
* });
* ```
* @example
* ```ts
* // Example of using aroundEach with fixtures
* aroundEach(async (runTest, { db }) => {
*   await db.transaction(() => runTest());
* });
* ```
*/
function aroundEach(fn, timeout) {
	y(fn, "\"aroundEach\" callback", ["function"]);
	const stackTraceError = /* @__PURE__ */ new Error("STACK_TRACE_ERROR");
	const resolvedTimeout = timeout ?? getDefaultHookTimeout();
	const wrapper = (runTest, context, suite) => {
		const innerFn = (ctx) => fn(runTest, ctx, suite);
		configureProps(innerFn, {
			index: 1,
			original: fn
		});
		return withFixtures(innerFn, { suite })(context);
	};
	return getCurrentSuite().on("aroundEach", Object.assign(wrapper, {
		[AROUND_TIMEOUT_KEY]: resolvedTimeout,
		[AROUND_STACK_TRACE_KEY]: stackTraceError
	}));
}
function withSuiteFixtures(suiteHook, fn, context, stackTraceError, contextIndex = 0) {
	return (...args) => {
		const suite = args.at(-1);
		const prefix = args.slice(0, -1);
		const wrapper = (ctx) => fn(...prefix, ctx, suite);
		configureProps(wrapper, {
			index: contextIndex,
			original: fn
		});
		const fixtures = context?.getFixtures();
		const fileContext = fixtures?.getFileContext(suite.file);
		return withFixtures(wrapper, {
			suiteHook,
			fixtures,
			context: fileContext,
			stackTraceError
		})();
	};
}
function getAroundHookTimeout(hook) {
	return AROUND_TIMEOUT_KEY in hook && typeof hook[AROUND_TIMEOUT_KEY] === "number" ? hook[AROUND_TIMEOUT_KEY] : getDefaultHookTimeout();
}
function getAroundHookStackTrace(hook) {
	return AROUND_STACK_TRACE_KEY in hook && hook[AROUND_STACK_TRACE_KEY] instanceof Error ? hook[AROUND_STACK_TRACE_KEY] : void 0;
}
function findTestFileStackTrace(testFilePath, error) {
	let stack;
	try {
		stack = error.stack;
	} catch {
		return;
	}
	if (!stack) return;
	return p$1(stack, { ignoreStackEntries: [] }).find((stack) => stack.file === testFilePath);
}
function validateTags(config, tags) {
	if (!config.strictTags) return;
	const availableTags = new Set(config.tags.map((tag) => tag.name));
	for (const tag of tags) if (!availableTags.has(tag)) throw createNoTagsError(config.tags, tag);
}
function createNoTagsError(availableTags, tag, prefix = "tag") {
	if (!availableTags.length) throw new Error(`The Vitest config doesn't define any "tags", cannot apply "${tag}" ${prefix} for this test. See: https://vitest.dev/guide/test-tags`);
	throw new Error(`The ${prefix} "${tag}" is not defined in the configuration. Available tags are:\n${availableTags.map((t) => `- ${t.name}${t.description ? `: ${t.description}` : ""}`).join("\n")}`);
}
function createTagsFilter(tagsExpr, availableTags) {
	const matchers = tagsExpr.map((expr) => parseTagsExpression(expr, availableTags));
	return (testTags) => {
		return matchers.every((matcher) => matcher(testTags));
	};
}
function parseTagsExpression(expr, availableTags) {
	const stream = new TokenStream(tokenize(expr), expr);
	const ast = parseOrExpression(stream, availableTags);
	if (stream.peek().type !== "EOF") throw new Error(`Invalid tags expression: unexpected "${formatToken(stream.peek())}" in "${expr}"`);
	return (tags) => evaluateNode(ast, tags);
}
function formatToken(token) {
	switch (token.type) {
		case "TAG": return token.value;
		default: return formatTokenType(token.type);
	}
}
function tokenize(expr) {
	const tokens = [];
	let i = 0;
	while (i < expr.length) {
		if (expr[i] === " " || expr[i] === "	") {
			i++;
			continue;
		}
		if (expr[i] === "(") {
			tokens.push({ type: "LPAREN" });
			i++;
			continue;
		}
		if (expr[i] === ")") {
			tokens.push({ type: "RPAREN" });
			i++;
			continue;
		}
		if (expr[i] === "!") {
			tokens.push({ type: "NOT" });
			i++;
			continue;
		}
		if (expr.slice(i, i + 2) === "&&") {
			tokens.push({ type: "AND" });
			i += 2;
			continue;
		}
		if (expr.slice(i, i + 2) === "||") {
			tokens.push({ type: "OR" });
			i += 2;
			continue;
		}
		if (/^and(?:\s|\)|$)/i.test(expr.slice(i))) {
			tokens.push({ type: "AND" });
			i += 3;
			continue;
		}
		if (/^or(?:\s|\)|$)/i.test(expr.slice(i))) {
			tokens.push({ type: "OR" });
			i += 2;
			continue;
		}
		if (/^not\s/i.test(expr.slice(i))) {
			tokens.push({ type: "NOT" });
			i += 3;
			continue;
		}
		let tag = "";
		while (i < expr.length && expr[i] !== " " && expr[i] !== "	" && expr[i] !== "(" && expr[i] !== ")" && expr[i] !== "!" && expr[i] !== "&" && expr[i] !== "|") {
			const remaining = expr.slice(i);
			if (tag === "" && (/^and(?:\s|\)|$)/i.test(remaining) || /^or(?:\s|\)|$)/i.test(remaining) || /^not\s/i.test(remaining))) break;
			tag += expr[i];
			i++;
		}
		if (tag) tokens.push({
			type: "TAG",
			value: tag
		});
	}
	tokens.push({ type: "EOF" });
	return tokens;
}
var TokenStream = class {
	tokens;
	expr;
	pos = 0;
	constructor(tokens, expr) {
		this.tokens = tokens;
		this.expr = expr;
	}
	peek() {
		return this.tokens[this.pos];
	}
	next() {
		return this.tokens[this.pos++];
	}
	expect(type) {
		const token = this.next();
		if (token.type !== type) {
			if (type === "RPAREN" && token.type === "EOF") throw new Error(`Invalid tags expression: missing closing ")" in "${this.expr}"`);
			throw new Error(`Invalid tags expression: expected "${formatTokenType(type)}" but got "${formatToken(token)}" in "${this.expr}"`);
		}
		return token;
	}
	unexpectedToken() {
		const token = this.peek();
		if (token.type === "EOF") throw new Error(`Invalid tags expression: unexpected end of expression in "${this.expr}"`);
		throw new Error(`Invalid tags expression: unexpected "${formatToken(token)}" in "${this.expr}"`);
	}
};
function formatTokenType(type) {
	switch (type) {
		case "TAG": return "tag";
		case "AND": return "and";
		case "OR": return "or";
		case "NOT": return "not";
		case "LPAREN": return "(";
		case "RPAREN": return ")";
		case "EOF": return "end of expression";
	}
}
function parseOrExpression(stream, availableTags) {
	let left = parseAndExpression(stream, availableTags);
	while (stream.peek().type === "OR") {
		stream.next();
		const right = parseAndExpression(stream, availableTags);
		left = {
			type: "or",
			left,
			right
		};
	}
	return left;
}
function parseAndExpression(stream, availableTags) {
	let left = parseUnaryExpression(stream, availableTags);
	while (stream.peek().type === "AND") {
		stream.next();
		const right = parseUnaryExpression(stream, availableTags);
		left = {
			type: "and",
			left,
			right
		};
	}
	return left;
}
function parseUnaryExpression(stream, availableTags) {
	if (stream.peek().type === "NOT") {
		stream.next();
		return {
			type: "not",
			operand: parseUnaryExpression(stream, availableTags)
		};
	}
	return parsePrimaryExpression(stream, availableTags);
}
function parsePrimaryExpression(stream, availableTags) {
	const token = stream.peek();
	if (token.type === "LPAREN") {
		stream.next();
		const expr = parseOrExpression(stream, availableTags);
		stream.expect("RPAREN");
		return expr;
	}
	if (token.type === "TAG") {
		stream.next();
		const tagValue = token.value;
		return {
			type: "tag",
			value: tagValue,
			pattern: resolveTagPattern(tagValue, availableTags)
		};
	}
	stream.unexpectedToken();
}
function createWildcardRegex(pattern) {
	return new RegExp(`^${pattern.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*")}$`);
}
function resolveTagPattern(tagPattern, availableTags) {
	if (tagPattern.includes("*")) {
		const regex = createWildcardRegex(tagPattern);
		if (!availableTags.some((tag) => regex.test(tag.name))) throw createNoTagsError(availableTags, tagPattern, "tag pattern");
		return regex;
	}
	if (!availableTags.length || !availableTags.some((tag) => tag.name === tagPattern)) throw createNoTagsError(availableTags, tagPattern, "tag pattern");
	return null;
}
function evaluateNode(node, tags) {
	switch (node.type) {
		case "tag":
			if (node.pattern) return tags.some((tag) => node.pattern.test(tag));
			return tags.includes(node.value);
		case "not": return !evaluateNode(node.operand, tags);
		case "and": return evaluateNode(node.left, tags) && evaluateNode(node.right, tags);
		case "or": return evaluateNode(node.left, tags) || evaluateNode(node.right, tags);
	}
}
/**
* Creates a suite of tests, allowing for grouping and hierarchical organization of tests.
* Suites can contain both tests and other suites, enabling complex test structures.
*
* @param {string} name - The name of the suite, used for identification and reporting.
* @param {Function} fn - A function that defines the tests and suites within this suite.
* @example
* ```ts
* // Define a suite with two tests
* suite('Math operations', () => {
*   test('should add two numbers', () => {
*     expect(add(1, 2)).toBe(3);
*   });
*
*   test('should subtract two numbers', () => {
*     expect(subtract(5, 2)).toBe(3);
*   });
* });
* ```
* @example
* ```ts
* // Define nested suites
* suite('String operations', () => {
*   suite('Trimming', () => {
*     test('should trim whitespace from start and end', () => {
*       expect('  hello  '.trim()).toBe('hello');
*     });
*   });
*
*   suite('Concatenation', () => {
*     test('should concatenate two strings', () => {
*       expect('hello' + ' ' + 'world').toBe('hello world');
*     });
*   });
* });
* ```
*/
const suite = createSuite();
createTest(function(name, optionsOrFn, optionsOrTest) {
	if (getCurrentTest()) throw new Error("Calling the test function inside another test function is not allowed. Please put it inside \"describe\" or \"suite\" so it can be properly collected.");
	getCurrentSuite().test.fn.call(this, formatName(name), optionsOrFn, optionsOrTest);
});
let runner;
let defaultSuite;
let currentTestFilepath;
function assert(condition, message) {
	if (!condition) throw new Error(`Vitest failed to find ${message}. One of the following is possible:
- "vitest" is imported directly without running "vitest" command
- "vitest" is imported inside "globalSetup" (to fix this, use "setupFiles" instead, because "globalSetup" runs in a different context)
- "vitest" is imported inside Vite / Vitest config file
- Otherwise, it might be a Vitest bug. Please report it to https://github.com/vitest-dev/vitest/issues
`);
}
function getDefaultSuite() {
	assert(defaultSuite, "the default suite");
	return defaultSuite;
}
function getRunner() {
	assert(runner, "the runner");
	return runner;
}
function createDefaultSuite(runner) {
	const config = runner.config.sequence;
	const options = {};
	if (config.concurrent != null) options.concurrent = config.concurrent;
	const collector = suite("", options, () => {});
	delete collector.suite;
	return collector;
}
function clearCollectorContext(file, currentRunner) {
	currentTestFilepath = file.filepath;
	runner = currentRunner;
	if (!defaultSuite) defaultSuite = createDefaultSuite(currentRunner);
	defaultSuite.file = file;
	collectorContext.tasks.length = 0;
	defaultSuite.clear();
	collectorContext.currentSuite = defaultSuite;
}
function getCurrentSuite() {
	const currentSuite = collectorContext.currentSuite || defaultSuite;
	assert(currentSuite, "the current suite");
	return currentSuite;
}
function createSuiteHooks() {
	return {
		beforeAll: [],
		afterAll: [],
		beforeEach: [],
		afterEach: [],
		aroundEach: [],
		aroundAll: []
	};
}
const POSITIVE_INFINITY = Number.POSITIVE_INFINITY;
function assignDefined(target, source) {
	for (const key in source) if (source[key] !== void 0) target[key] = source[key];
	return target;
}
function parseArguments(optionsOrFn, timeoutOrTest) {
	if (timeoutOrTest != null && typeof timeoutOrTest === "object") throw new TypeError(`Signature "test(name, fn, { ... })" was deprecated in Vitest 3 and removed in Vitest 4. Please, provide options as a second argument instead.`);
	let options = {};
	let fn;
	if (typeof timeoutOrTest === "number") options = { timeout: timeoutOrTest };
	else if (typeof optionsOrFn === "object") options = optionsOrFn;
	if (typeof optionsOrFn === "function") {
		if (typeof timeoutOrTest === "function") throw new TypeError("Cannot use two functions as arguments. Please use the second argument for options.");
		fn = optionsOrFn;
	} else if (typeof timeoutOrTest === "function") fn = timeoutOrTest;
	return {
		options,
		handler: fn
	};
}
function createSuiteCollector(name, factory = () => {}, mode, each, suiteOptions) {
	const tasks = [];
	let suite;
	initSuite(true);
	const task = function(name = "", options = {}) {
		const currentSuite = collectorContext.currentSuite?.suite;
		const parentTags = (currentSuite ?? collectorContext.currentSuite?.file)?.tags || [];
		const testTags = A([...parentTags, ...t$1(options.tags)]);
		const tagsOptions = testTags.map((tag) => {
			const tagDefinition = runner.config.tags?.find((t) => t.name === tag);
			if (!tagDefinition && runner.config.strictTags) throw createNoTagsError(runner.config.tags, tag);
			return tagDefinition;
		}).filter((r) => r != null).sort((tag1, tag2) => (tag2.priority ?? POSITIVE_INFINITY) - (tag1.priority ?? POSITIVE_INFINITY)).reduce((acc, tag) => {
			const { name, description, priority, meta, ...options } = tag;
			assignDefined(acc, options);
			if (meta) acc.meta = Object.assign(acc.meta ?? Object.create(null), meta);
			return acc;
		}, {});
		const testOwnMeta = options.meta;
		const parentOptions = collectorContext.currentSuite?.options;
		options = assignDefined({
			...parentOptions,
			...tagsOptions
		}, options);
		const timeout = options.timeout ?? runner.config.testTimeout;
		const parentMeta = currentSuite?.meta;
		const tagMeta = tagsOptions.meta;
		const testMeta = Object.create(null);
		if (tagMeta) Object.assign(testMeta, tagMeta);
		if (parentMeta) Object.assign(testMeta, parentMeta);
		if (testOwnMeta) Object.assign(testMeta, testOwnMeta);
		const task = {
			id: "",
			name,
			fullName: createTaskName([currentSuite?.fullName ?? collectorContext.currentSuite?.file?.fullName, name]),
			fullTestName: createTaskName([currentSuite?.fullTestName, name]),
			suite: currentSuite,
			each: options.each,
			fails: options.fails,
			context: void 0,
			type: "test",
			file: currentSuite?.file ?? collectorContext.currentSuite?.file,
			timeout,
			retry: options.retry ?? runner.config.retry,
			repeats: options.repeats ?? runner.config.repeats,
			mode: options.only ? "only" : options.skip ? "skip" : options.todo ? "todo" : "run",
			meta: testMeta,
			annotations: [],
			artifacts: [],
			benchmarks: [],
			tags: testTags
		};
		const handler = options.handler;
		if (task.mode === "run" && !handler) task.mode = "todo";
		if (options.concurrent ?? runner.config.sequence.concurrent) task.concurrent = true;
		task.shuffle = suiteOptions?.shuffle;
		const context = createTestContext(task, runner);
		Object.defineProperty(task, "context", {
			value: context,
			enumerable: false
		});
		setTestFixture(context, options.fixtures ?? new TestFixtures());
		const limit = Error.stackTraceLimit;
		Error.stackTraceLimit = 10;
		const stackTraceError = /* @__PURE__ */ new Error("STACK_TRACE_ERROR");
		Error.stackTraceLimit = limit;
		if (handler) setFn(task, withTimeout(withCancel(withAwaitAsyncAssertions(withFixtures(handler, { context }), task), task.context.signal), timeout, false, stackTraceError, (_, error) => abortIfTimeout([context], error)));
		if (runner.config.includeTaskLocation) {
			const stack = findTestFileStackTrace(currentTestFilepath, stackTraceError);
			if (stack) task.location = {
				line: stack.line,
				column: stack.column
			};
		}
		tasks.push(task);
		return task;
	};
	const test = createTest(function(name, optionsOrFn, timeoutOrTest) {
		const { options, handler } = parseArguments(optionsOrFn, timeoutOrTest);
		const concurrent = this.concurrent ?? options?.concurrent;
		if (concurrent != null) options.concurrent = concurrent;
		const test = task(formatName(name), {
			...assignDefined({ ...this }, options),
			handler
		});
		test.type = "test";
	});
	const collector = {
		type: "collector",
		name,
		mode,
		suite,
		options: suiteOptions,
		test,
		file: suite.file,
		tasks,
		collect,
		task,
		clear,
		on: addHook
	};
	function addHook(name, ...fn) {
		getHooks(suite)[name].push(...fn);
	}
	function initSuite(includeLocation) {
		if (typeof suiteOptions === "number") suiteOptions = { timeout: suiteOptions };
		const currentSuite = collectorContext.currentSuite?.suite;
		const parentTask = currentSuite ?? collectorContext.currentSuite?.file;
		const suiteTags = t$1(suiteOptions?.tags);
		validateTags(runner.config, suiteTags);
		suite = {
			id: "",
			type: "suite",
			name,
			fullName: createTaskName([currentSuite?.fullName ?? collectorContext.currentSuite?.file?.fullName, name]),
			fullTestName: createTaskName([currentSuite?.fullTestName, name]),
			suite: currentSuite,
			mode,
			each,
			file: currentSuite?.file ?? collectorContext.currentSuite?.file,
			shuffle: suiteOptions?.shuffle,
			tasks: [],
			meta: suiteOptions?.meta ?? Object.create(null),
			concurrent: suiteOptions?.concurrent,
			tags: A([...parentTask?.tags || [], ...suiteTags])
		};
		if (runner && includeLocation && runner.config.includeTaskLocation) {
			const limit = Error.stackTraceLimit;
			Error.stackTraceLimit = 15;
			const error = /* @__PURE__ */ new Error("stacktrace");
			Error.stackTraceLimit = limit;
			const stack = findTestFileStackTrace(currentTestFilepath, error);
			if (stack) suite.location = {
				line: stack.line,
				column: stack.column
			};
		}
		setHooks(suite, createSuiteHooks());
	}
	function clear() {
		tasks.length = 0;
		initSuite(false);
	}
	async function collect(file) {
		if (!file) throw new TypeError("File is required to collect tasks.");
		if (factory) await runWithSuite(collector, () => factory(test));
		const allChildren = [];
		let containsOnly = false;
		let containsTest = false;
		for (const i of tasks) {
			const child = i.type === "collector" ? await i.collect(file) : i;
			allChildren.push(child);
			if (child.mode === "only" || child.type === "suite" && child.containsOnly) containsOnly = true;
			if (child.type === "test" || child.type === "suite" && child.containsTest) containsTest = true;
		}
		suite.tasks = allChildren;
		suite.containsOnly = containsOnly;
		suite.containsTest = containsTest;
		return suite;
	}
	collectTask(collector);
	return collector;
}
function withAwaitAsyncAssertions(fn, task) {
	return (async (...args) => {
		const fnResult = await fn(...args);
		if (task.promises) {
			const errors = (await Promise.allSettled(task.promises)).map((r) => r.status === "rejected" ? r.reason : void 0).filter(Boolean);
			if (errors.length) throw errors;
		}
		return fnResult;
	});
}
function createSuite() {
	function suiteFn(name, factoryOrOptions, optionsOrFactory) {
		if (getCurrentTest()) throw new Error("Calling the suite function inside test function is not allowed. It can be only called at the top level or inside another suite function.");
		const currentSuite = collectorContext.currentSuite || defaultSuite;
		let { options, handler: factory } = parseArguments(factoryOrOptions, optionsOrFactory);
		const { meta: parentMeta, ...parentOptions } = currentSuite?.options || {};
		options = assignDefined({ ...parentOptions }, options);
		const shuffle = this.shuffle ?? options.shuffle ?? currentSuite?.options?.shuffle ?? runner?.config.sequence.shuffle;
		if (shuffle != null) options.shuffle = shuffle;
		let mode = this.only ?? options.only ? "only" : this.skip ?? options.skip ? "skip" : this.todo ?? options.todo ? "todo" : "run";
		if (mode === "run" && !factory) mode = "todo";
		const concurrent = this.concurrent ?? options.concurrent;
		if (concurrent != null) options.concurrent = concurrent;
		if (parentMeta) options.meta = Object.assign(Object.create(null), parentMeta, options.meta);
		return createSuiteCollector(formatName(name), factory, mode, this.each, options);
	}
	suiteFn.each = function(cases, ...args) {
		const context = getChainableContext(this);
		const suite = context.withContext();
		context.setContext("each", true);
		if (Array.isArray(cases) && args.length) cases = formatTemplateString(cases, args);
		return (name, optionsOrFn, fnOrOptions) => {
			const _name = formatName(name);
			const arrayOnlyCases = cases.every(Array.isArray);
			const { options, handler } = parseArguments(optionsOrFn, fnOrOptions);
			const fnFirst = typeof optionsOrFn === "function";
			cases.forEach((i, idx) => {
				const items = Array.isArray(i) ? i : [i];
				if (fnFirst) {
					if (arrayOnlyCases) suite(formatTitle(_name, items, idx), handler ? () => handler(...items) : void 0, options.timeout);
					else suite(formatTitle(_name, items, idx), handler ? () => handler(i) : void 0, options.timeout);
				} else if (arrayOnlyCases) suite(formatTitle(_name, items, idx), options, handler ? () => handler(...items) : void 0);
				else suite(formatTitle(_name, items, idx), options, handler ? () => handler(i) : void 0);
			});
			context.setContext("each", void 0);
		};
	};
	suiteFn.for = function(cases, ...args) {
		const suite = getChainableContext(this).withContext();
		if (Array.isArray(cases) && args.length) cases = formatTemplateString(cases, args);
		return (name, optionsOrFn, fnOrOptions) => {
			const name_ = formatName(name);
			const { options, handler } = parseArguments(optionsOrFn, fnOrOptions);
			cases.forEach((item, idx) => {
				suite(formatTitle(name_, t$1(item), idx), options, handler ? () => handler(item) : void 0);
			});
		};
	};
	suiteFn.skipIf = (condition) => condition ? suite.skip : suite;
	suiteFn.runIf = (condition) => condition ? suite : suite.skip;
	return createChainable([
		"concurrent",
		"shuffle",
		"skip",
		"only",
		"todo"
	], suiteFn);
}
function createTaskCollector(fn) {
	const taskFn = fn;
	taskFn.each = function(cases, ...args) {
		const context = getChainableContext(this);
		const test = context.withContext();
		context.setContext("each", true);
		if (Array.isArray(cases) && args.length) cases = formatTemplateString(cases, args);
		return (name, optionsOrFn, fnOrOptions) => {
			const _name = formatName(name);
			const arrayOnlyCases = cases.every(Array.isArray);
			const { options, handler } = parseArguments(optionsOrFn, fnOrOptions);
			const fnFirst = typeof optionsOrFn === "function";
			cases.forEach((i, idx) => {
				const items = Array.isArray(i) ? i : [i];
				if (fnFirst) {
					if (arrayOnlyCases) test(formatTitle(_name, items, idx), handler ? () => handler(...items) : void 0, options.timeout);
					else test(formatTitle(_name, items, idx), handler ? () => handler(i) : void 0, options.timeout);
				} else if (arrayOnlyCases) test(formatTitle(_name, items, idx), options, handler ? () => handler(...items) : void 0);
				else test(formatTitle(_name, items, idx), options, handler ? () => handler(i) : void 0);
			});
			context.setContext("each", void 0);
		};
	};
	taskFn.for = function(cases, ...args) {
		const test = getChainableContext(this).withContext();
		if (Array.isArray(cases) && args.length) cases = formatTemplateString(cases, args);
		return (name, optionsOrFn, fnOrOptions) => {
			const _name = formatName(name);
			const { options, handler } = parseArguments(optionsOrFn, fnOrOptions);
			cases.forEach((item, idx) => {
				const handlerWrapper = handler ? (ctx) => handler(item, ctx) : void 0;
				if (handlerWrapper) configureProps(handlerWrapper, {
					index: 1,
					original: handler
				});
				test(formatTitle(_name, t$1(item), idx), options, handlerWrapper);
			});
		};
	};
	taskFn.skipIf = function(condition) {
		return getChainableContext(this).extendContext({ skip: !!condition });
	};
	taskFn.runIf = function(condition) {
		return getChainableContext(this).extendContext({ skip: !condition });
	};
	/**
	* Parse builder pattern arguments into a fixtures object.
	* Handles both builder pattern (name, options?, value) and object syntax.
	*/
	function parseBuilderFixtures(fixturesOrName, optionsOrFn, maybeFn) {
		if (typeof fixturesOrName !== "string") return fixturesOrName;
		const fixtureName = fixturesOrName;
		let fixtureOptions;
		let fixtureValue;
		if (maybeFn !== void 0) {
			fixtureOptions = optionsOrFn;
			fixtureValue = maybeFn;
		} else if (optionsOrFn !== null && typeof optionsOrFn === "object" && !Array.isArray(optionsOrFn) && TestFixtures.isFixtureOptions(optionsOrFn)) {
			fixtureOptions = optionsOrFn;
			fixtureValue = {};
		} else {
			fixtureOptions = void 0;
			fixtureValue = optionsOrFn;
		}
		if (typeof fixtureValue === "function") {
			const builderFn = fixtureValue;
			const fixture = async (ctx, use) => {
				let cleanup;
				const onCleanup = (fn) => {
					if (cleanup !== void 0) throw new Error("onCleanup can only be called once per fixture. Define separate fixtures if you need multiple cleanup functions.");
					cleanup = fn;
				};
				await use(await builderFn(ctx, { onCleanup }));
				if (cleanup) await cleanup();
			};
			configureProps(fixture, { original: builderFn });
			if (fixtureOptions) return { [fixtureName]: [fixture, fixtureOptions] };
			return { [fixtureName]: fixture };
		}
		if (fixtureOptions) return { [fixtureName]: [fixtureValue, fixtureOptions] };
		return { [fixtureName]: fixtureValue };
	}
	taskFn.override = function(fixturesOrName, optionsOrFn, maybeFn) {
		const userFixtures = parseBuilderFixtures(fixturesOrName, optionsOrFn, maybeFn);
		getChainableContext(this).getFixtures().override(runner, userFixtures);
		return this;
	};
	taskFn.scoped = function(fixtures) {
		console.warn(`test.scoped() is deprecated and will be removed in future versions. Please use test.override() instead.`);
		return this.override(fixtures);
	};
	taskFn.extend = function(fixturesOrName, optionsOrFn, maybeFn) {
		const userFixtures = parseBuilderFixtures(fixturesOrName, optionsOrFn, maybeFn);
		const fixtures = getChainableContext(this).getFixtures().extend(runner, userFixtures);
		const _test = createTest(function(name, optionsOrFn, optionsOrTest) {
			fn.call(this, formatName(name), optionsOrFn, optionsOrTest);
		});
		getChainableContext(_test).mergeContext({ fixtures });
		return _test;
	};
	taskFn.describe = suite;
	taskFn.suite = suite;
	taskFn.beforeEach = beforeEach;
	taskFn.afterEach = afterEach;
	taskFn.beforeAll = beforeAll;
	taskFn.afterAll = afterAll;
	taskFn.aroundEach = aroundEach;
	taskFn.aroundAll = aroundAll;
	return createChainable([
		"concurrent",
		"skip",
		"only",
		"todo",
		"fails"
	], taskFn, { fixtures: new TestFixtures() });
}
function createTest(fn) {
	return createTaskCollector(fn);
}
function formatName(name) {
	return typeof name === "string" ? name : typeof name === "function" ? name.name || "<anonymous>" : String(name);
}
function formatTitle(template, items, idx) {
	if (template.includes("%#") || template.includes("%$")) template = template.replace(/%%/g, "__vitest_escaped_%__").replace(/%#/g, `${idx}`).replace(/%\$/g, `${idx + 1}`).replace(/__vitest_escaped_%__/g, "%%");
	const count = template.split("%").length - 1;
	if (template.includes("%f")) (template.match(/%f/g) || []).forEach((_, i) => {
		if (z(items[i]) || Object.is(items[i], -0)) {
			let occurrence = 0;
			template = template.replace(/%f/g, (match) => {
				occurrence++;
				return occurrence === i + 1 ? "-%f" : match;
			});
		}
	});
	const inspectOptions = { truncate: runner.config.taskTitleValueFormatTruncate };
	const isObjectItem = f$2(items[0]);
	function formatAttribute(s) {
		return s.replace(/\$([$\p{ID_Continue}.]+)/gu, (_, key) => {
			const isArrayKey = /^\d+$/.test(key);
			if (!isObjectItem && !isArrayKey) return `$${key}`;
			const arrayElement = isArrayKey ? B(items, key) : void 0;
			const value = isObjectItem ? B(items[0], key, arrayElement) : arrayElement;
			if (typeof value === "string") return inspectOptions.truncate ? t(value, inspectOptions.truncate) : value;
			return i$1(value, inspectOptions);
		});
	}
	let output = "";
	let i = 0;
	handleRegexMatch(template, a$3, (match) => {
		if (i < count) output += f$1([match[0], items[i++]], inspectOptions);
		else output += match[0];
	}, (nonMatch) => {
		output += formatAttribute(nonMatch);
	});
	return output;
}
function handleRegexMatch(input, regex, onMatch, onNonMatch) {
	let lastIndex = 0;
	for (const m of input.matchAll(regex)) {
		if (lastIndex < m.index) onNonMatch(input.slice(lastIndex, m.index));
		onMatch(m);
		lastIndex = m.index + m[0].length;
	}
	if (lastIndex < input.length) onNonMatch(input.slice(lastIndex));
}
function formatTemplateString(cases, args) {
	const header = cases.join("").trim().replace(/ /g, "").split("\n").map((i) => i.split("|"))[0];
	const res = [];
	for (let i = 0; i < Math.floor(args.length / header.length); i++) {
		const oneCase = {};
		for (let j = 0; j < header.length; j++) oneCase[header[j]] = args[i * header.length + j];
		res.push(oneCase);
	}
	return res;
}
/**
* @experimental
* @advanced
*
* Records a custom test artifact during test execution.
*
* This function allows you to attach structured data, files, or metadata to a test.
*
* Vitest automatically injects the source location where the artifact was created and manages any attachments you include.
*
* **Note:** artifacts must be recorded before the task is reported. Any artifacts recorded after that will not be included in the task.
*
* @param task - The test task context, typically accessed via `this.task` in custom matchers or `context.task` in tests
* @param artifact - The artifact to record. Must extend {@linkcode TestArtifactBase}
*
* @returns A promise that resolves to the recorded artifact with location injected
*
* @throws {Error} If the test runner doesn't support artifacts
*
* @example
* ```ts
* // In a custom assertion
* async function toHaveValidSchema(this: MatcherState, actual: unknown) {
*   const validation = validateSchema(actual)
*
*   await recordArtifact(this.task, {
*     type: 'my-plugin:schema-validation',
*     passed: validation.valid,
*     errors: validation.errors,
*   })
*
*   return { pass: validation.valid, message: () => '...' }
* }
* ```
*/
async function recordArtifact(task, artifact) {
	const runner = getRunner();
	const stack = findTestFileStackTrace(task.file.filepath, /* @__PURE__ */ new Error("STACK_TRACE"));
	if (stack) {
		artifact.location = {
			file: stack.file,
			line: stack.line,
			column: stack.column
		};
		if (artifact.type === "internal:annotation") artifact.annotation.location = artifact.location;
	}
	if (Array.isArray(artifact.attachments)) for (const attachment of artifact.attachments) manageArtifactAttachment(attachment);
	if (artifact.type === "internal:annotation") return artifact;
	if (!runner.onTestArtifactRecord) throw new Error(`Test runner doesn't support test artifacts.`);
	await finishSendTasksUpdate(runner);
	const resolvedArtifact = await runner.onTestArtifactRecord(task, artifact);
	task.artifacts.push(resolvedArtifact);
	return resolvedArtifact;
}
const table = [];
for (let i = 65; i < 91; i++) table.push(String.fromCharCode(i));
for (let i = 97; i < 123; i++) table.push(String.fromCharCode(i));
for (let i = 0; i < 10; i++) table.push(i.toString(10));
table.push("+", "/");
function encodeUint8Array(bytes) {
	let base64 = "";
	const len = bytes.byteLength;
	for (let i = 0; i < len; i += 3) if (len === i + 1) {
		const a = (bytes[i] & 252) >> 2;
		const b = (bytes[i] & 3) << 4;
		base64 += table[a];
		base64 += table[b];
		base64 += "==";
	} else if (len === i + 2) {
		const a = (bytes[i] & 252) >> 2;
		const b = (bytes[i] & 3) << 4 | (bytes[i + 1] & 240) >> 4;
		const c = (bytes[i + 1] & 15) << 2;
		base64 += table[a];
		base64 += table[b];
		base64 += table[c];
		base64 += "=";
	} else {
		const a = (bytes[i] & 252) >> 2;
		const b = (bytes[i] & 3) << 4 | (bytes[i + 1] & 240) >> 4;
		const c = (bytes[i + 1] & 15) << 2 | (bytes[i + 2] & 192) >> 6;
		const d = bytes[i + 2] & 63;
		base64 += table[a];
		base64 += table[b];
		base64 += table[c];
		base64 += table[d];
	}
	return base64;
}
/**
* Records an async operation associated with a test task.
*
* This function tracks promises that should be awaited before a test completes.
* The promise is automatically removed from the test's promise list once it settles.
*/
function recordAsyncOperation(test, promise) {
	promise = promise.finally(() => {
		if (!test.promises) return;
		const index = test.promises.indexOf(promise);
		if (index !== -1) test.promises.splice(index, 1);
	});
	if (!test.promises) test.promises = [];
	test.promises.push(promise);
	return promise;
}
/**
* Validates and prepares a test attachment for serialization.
*
* This function ensures attachments have either `body` or `path` set (but not both), and converts `Uint8Array` bodies to base64-encoded strings for easier serialization.
*
* @param attachment - The attachment to validate and prepare
*
* @throws {TypeError} If neither `body` nor `path` is provided
* @throws {TypeError} If both `body` and `path` are provided
*/
function manageArtifactAttachment(attachment) {
	if (attachment.body == null && !attachment.path) throw new TypeError(`Test attachment requires "body" or "path" to be set. Both are missing.`);
	if (attachment.body && attachment.path) throw new TypeError(`Test attachment requires only one of "body" or "path" to be set. Both are specified.`);
	if (attachment.path && attachment.bodyEncoding) throw new TypeError(`Test attachment with "path" should not have "bodyEncoding" specified.`);
	if (attachment.body instanceof Uint8Array) attachment.body = encodeUint8Array(attachment.body);
	if (attachment.body != null) attachment.bodyEncoding ??= "base64";
}
const now$2 = globalThis.performance ? globalThis.performance.now.bind(globalThis.performance) : Date.now;
const DERIVED_BUFFER = 100;
const SETTLE_GRACE = 500;
var TaskDeadline = class {
	startTime = now$2();
	endTime;
	timer;
	operations = /* @__PURE__ */ new Set();
	constructor(timeout, onTimeout) {
		this.endTime = this.startTime + timeout;
		const { setTimeout } = a$4();
		this.timer = setTimeout(onTimeout, timeout);
		this.timer.unref?.();
	}
	remaining() {
		return this.endTime - now$2();
	}
	exceeded() {
		return now$2() >= this.endTime;
	}
	clear() {
		const { clearTimeout } = a$4();
		clearTimeout(this.timer);
	}
	/** timeout for an operation that must fail before the task does */
	derive() {
		return Math.max(Math.floor(this.remaining()) - DERIVED_BUFFER, 1);
	}
	/** Registers an operation with its own timeout; see `settle`. */
	track(name, promise, timeout, source) {
		const operation = {
			name,
			source,
			promise,
			endTime: now$2() + timeout
		};
		this.operations.add(operation);
		promise.finally(() => this.operations.delete(operation)).catch(() => {});
		return promise;
	}
	/**
	* Waits for the operations that were due before the task, if possible.
	*/
	settle() {
		if (!this.operations.size) return;
		const operations = [...this.operations];
		const pending = () => operations.filter((operation) => this.operations.has(operation));
		const due = operations.filter((operation) => operation.endTime <= this.endTime);
		if (!due.length) return Promise.resolve(operations);
		const { setTimeout } = a$4();
		const waitUntil = Math.max(...due.map((operation) => operation.endTime)) + SETTLE_GRACE;
		return Promise.race([Promise.all(due.map((operation) => operation.promise)).then(pending), new Promise((resolve) => setTimeout(() => {
			resolve(pending());
		}, Math.max(waitUntil - now$2(), 0)))]);
	}
};
const collectorContext = {
	tasks: [],
	currentSuite: null
};
function collectTask(task) {
	collectorContext.currentSuite?.tasks.push(task);
}
async function runWithSuite(suite, fn) {
	const prev = collectorContext.currentSuite;
	collectorContext.currentSuite = suite;
	await fn();
	collectorContext.currentSuite = prev;
}
function withTimeout(fn, timeout, isHook = false, stackTraceError, onTimeout) {
	if (timeout <= 0 || timeout === Number.POSITIVE_INFINITY) return fn;
	return function runWithTimeout(...args) {
		const runner = getRunner();
		const previousDeadline = runner._deadline;
		return new Promise((resolve_, reject_) => {
			let settled = false;
			const deadline = new TaskDeadline(timeout, () => {
				const pending = deadline.settle();
				if (pending) {
					pending.then(rejectTimeoutError, reject);
					return;
				}
				rejectTimeoutError();
			});
			runner._deadline = deadline;
			function rejectTimeoutError(pending) {
				if (settled) return;
				settled = true;
				const error = makeTimeoutError(isHook, timeout, stackTraceError, pending);
				onTimeout?.(args, error);
				reject_(error);
			}
			function resolve(result) {
				runner._deadline = previousDeadline;
				deadline.clear();
				if (deadline.exceeded()) {
					rejectTimeoutError();
					return;
				}
				settled = true;
				resolve_(result);
			}
			function reject(error) {
				if (settled) return;
				settled = true;
				runner._deadline = previousDeadline;
				deadline.clear();
				reject_(error);
			}
			try {
				const result = fn(...args);
				if (typeof result === "object" && result != null && typeof result.then === "function") result.then(resolve, reject);
				else resolve(result);
			} catch (error) {
				reject(error);
			}
		});
	};
}
function withCancel(fn, signal) {
	return function runWithCancel(...args) {
		return new Promise((resolve, reject) => {
			const onAbort = () => reject(signal.reason);
			signal.addEventListener("abort", onAbort, { once: true });
			const cleanup = () => signal.removeEventListener("abort", onAbort);
			try {
				const result = fn(...args);
				if (typeof result === "object" && result != null && typeof result.then === "function") result.then((value) => {
					cleanup();
					resolve(value);
				}, (error) => {
					cleanup();
					reject(error);
				});
				else {
					cleanup();
					resolve(result);
				}
			} catch (error) {
				cleanup();
				reject(error);
			}
		});
	};
}
const abortControllers = /* @__PURE__ */ new WeakMap();
function abortIfTimeout([context], error) {
	if (context) abortContextSignal(context, error);
}
function abortContextSignal(context, error) {
	abortControllers.get(context)?.abort(error);
}
function createTestContext(test, runner) {
	const context = function() {
		throw new Error("done() callback is deprecated, use promise instead");
	};
	let abortController = abortControllers.get(context);
	if (!abortController) {
		abortController = new AbortController();
		abortControllers.set(context, abortController);
	}
	context.signal = abortController.signal;
	context.task = test;
	context.skip = (condition, note) => {
		if (condition === false) return;
		test.result ??= { state: "skip" };
		test.result.pending = true;
		throw new PendingError("test is skipped; abort execution", test, typeof condition === "string" ? condition : note);
	};
	context.annotate = ((message, type, attachment) => {
		if (test.result && test.result.state !== "run") throw new Error(`Cannot annotate tests outside of the test run. The test "${test.name}" finished running with the "${test.result.state}" state already.`);
		const annotation = {
			message,
			type: typeof type === "object" || type === void 0 ? "notice" : type
		};
		const annotationAttachment = typeof type === "object" ? type : attachment;
		if (annotationAttachment) {
			annotation.attachment = annotationAttachment;
			manageArtifactAttachment(annotation.attachment);
		}
		return recordAsyncOperation(test, recordArtifact(test, {
			type: "internal:annotation",
			annotation
		}).then(async ({ annotation }) => {
			if (!runner.onTestAnnotate) throw new Error(`Test runner doesn't support test annotations.`);
			await finishSendTasksUpdate(runner);
			const resolvedAnnotation = await runner.onTestAnnotate(test, annotation);
			test.annotations.push(resolvedAnnotation);
			return resolvedAnnotation;
		}));
	});
	context.onTestFailed = (handler, timeout) => {
		test.onFailed ||= [];
		test.onFailed.push(withTimeout(handler, timeout ?? runner.config.hookTimeout, true, /* @__PURE__ */ new Error("STACK_TRACE_ERROR"), (_, error) => abortController.abort(error)));
	};
	context.onTestFinished = (handler, timeout) => {
		test.onFinished ||= [];
		test.onFinished.push(withTimeout(handler, timeout ?? runner.config.hookTimeout, true, /* @__PURE__ */ new Error("STACK_TRACE_ERROR"), (_, error) => abortController.abort(error)));
	};
	return runner.extendTaskContext?.(context) || context;
}
function makeTimeoutError(isHook, timeout, stackTraceError, pending) {
	const waiting = pending?.length ? ` while waiting for ${pending.map((operation) => operation.name).join(", ")}` : "";
	const lastOperationSource = pending?.at(-1)?.source;
	if (lastOperationSource) stackTraceError = lastOperationSource;
	const message = `${isHook ? "Hook" : "Test"} timed out in ${timeout}ms${waiting}.\nIf this is a long-running ${isHook ? "hook" : "test"}, pass a timeout value as the last argument or configure it globally with "${isHook ? "hookTimeout" : "testTimeout"}".`;
	const error = new Error(message);
	if (stackTraceError?.stack) error.stack = stackTraceError.stack.replace(stackTraceError.message, error.message);
	return error;
}
async function runSetupFiles(config, files, runner) {
	if (config.sequence.setupFiles === "parallel") await Promise.all(files.map(async (fsPath) => {
		await runner.importFile(fsPath, "setup");
	}));
	else for (const fsPath of files) await runner.importFile(fsPath, "setup");
}
const now$1 = globalThis.performance ? globalThis.performance.now.bind(globalThis.performance) : Date.now;
async function collectTests(specs, runner) {
	const files = [];
	const config = runner.config;
	const $ = runner.trace;
	let defaultTagsFilter;
	for (const spec of specs) {
		const filepath = typeof spec === "string" ? spec : spec.filepath;
		await $("collect_spec", { "code.file.path": filepath }, async () => {
			runner._currentSpecification = typeof spec === "string" ? { filepath: spec } : spec;
			const testLocations = typeof spec === "string" ? void 0 : spec.testLocations;
			const testNamePattern = typeof spec === "string" ? void 0 : spec.testNamePattern;
			const testIds = typeof spec === "string" ? void 0 : spec.testIds;
			const testNames = typeof spec === "string" ? void 0 : spec.testNames;
			const testTagsFilter = typeof spec === "object" && spec.testTagsFilter ? createTagsFilter(spec.testTagsFilter, config.tags) : void 0;
			const fileTags = typeof spec === "string" ? [] : spec.fileTags || [];
			const file = createFileTask(filepath, config.root, config.name, runner.pool, runner.viteEnvironment, { __vitest_label__: config.mergeReportsLabel });
			file.tags = fileTags;
			file.shuffle = config.sequence.shuffle;
			try {
				validateTags(runner.config, fileTags);
				runner.onCollectStart?.(file);
				clearCollectorContext(file, runner);
				const setupFiles = t$1(config.setupFiles);
				const fetchBeforeSetup = runner.getModuleFetchDuration?.();
				if (setupFiles.length) {
					const setupStart = now$1();
					await runSetupFiles(config, setupFiles, runner);
					file.setupDuration = now$1() - setupStart;
				} else file.setupDuration = 0;
				const fetchBeforeCollect = runner.getModuleFetchDuration?.();
				if (fetchBeforeSetup != null && fetchBeforeCollect != null) file.setupFetchDuration = fetchBeforeCollect - fetchBeforeSetup;
				const collectStart = now$1();
				await runner.importFile(filepath, "collect");
				const durations = runner.getImportDurations?.();
				if (durations) file.importDurations = durations;
				const defaultTasks = await getDefaultSuite().collect(file);
				const fileHooks = createSuiteHooks();
				mergeHooks(fileHooks, getHooks(defaultTasks));
				for (const c of [...defaultTasks.tasks, ...collectorContext.tasks]) if (c.type === "test" || c.type === "suite") file.tasks.push(c);
				else if (c.type === "collector") {
					const suite = await c.collect(file);
					if (suite.name || suite.tasks.length) {
						mergeHooks(fileHooks, getHooks(suite));
						file.tasks.push(suite);
					}
				}
				setHooks(file, fileHooks);
				file.collectDuration = now$1() - collectStart;
				const fetchAfterCollect = runner.getModuleFetchDuration?.();
				if (fetchBeforeCollect != null && fetchAfterCollect != null) file.collectFetchDuration = fetchAfterCollect - fetchBeforeCollect;
			} catch (e) {
				file.result = {
					state: "fail",
					errors: e instanceof AggregateError ? e.errors.map((e) => processError(e, runner.config._diffOptions)) : [processError(e, runner.config._diffOptions)]
				};
				const durations = runner.getImportDurations?.();
				if (durations) file.importDurations = durations;
			}
			calculateSuiteHash(file);
			file.containsOnly = file.tasks.some((t) => t.mode === "only" || t.type === "suite" && t.containsOnly);
			file.containsTest = file.tasks.some((t) => t.type === "test" || t.type === "suite" && t.containsTest);
			const hasOnlyTasks = file.containsOnly;
			if (!testTagsFilter && !defaultTagsFilter && config.tagsFilter) defaultTagsFilter = createTagsFilter(config.tagsFilter, config.tags);
			interpretTaskModes(file, testNamePattern ?? config.testNamePattern, testLocations, testIds, testNames, testTagsFilter ?? defaultTagsFilter, hasOnlyTasks, false, config.allowOnly);
			if (file.mode === "queued") file.mode = "run";
			files.push(file);
		});
	}
	return files;
}
function mergeHooks(baseHooks, hooks) {
	for (const _key in hooks) {
		const key = _key;
		baseHooks[key].push(...hooks[key]);
	}
	return baseHooks;
}
/**
* Partition in tasks groups by consecutive concurrent
*/
function partitionSuiteChildren(suite) {
	let tasksGroup = [];
	const tasksGroups = [];
	for (const c of suite.tasks) if (tasksGroup.length === 0 || c.concurrent === tasksGroup[0].concurrent) tasksGroup.push(c);
	else {
		tasksGroups.push(tasksGroup);
		tasksGroup = [c];
	}
	if (tasksGroup.length > 0) tasksGroups.push(tasksGroup);
	return tasksGroups;
}
const now = globalThis.performance ? globalThis.performance.now.bind(globalThis.performance) : Date.now;
const unixNow = Date.now;
const { clearTimeout, setTimeout } = a$4();
let limitMaxConcurrency;
let limitTestConcurrency;
/**
* Normalizes retry configuration to extract individual values.
* Handles both number and object forms.
*/
function getRetryCount(retry) {
	if (retry === void 0) return 0;
	if (typeof retry === "number") return retry;
	return retry.count ?? 0;
}
function getRetryDelay(retry) {
	if (retry === void 0) return 0;
	if (typeof retry === "number") return 0;
	return retry.delay ?? 0;
}
function getRetryCondition(retry) {
	if (retry === void 0) return;
	if (typeof retry === "number") return;
	return retry.condition;
}
function updateSuiteHookState(task, name, state, runner) {
	if (!task.result) task.result = { state: "run" };
	if (!task.result.hooks) task.result.hooks = {};
	const suiteHooks = task.result.hooks;
	if (suiteHooks) {
		suiteHooks[name] = state;
		let event = state === "run" ? "before-hook-start" : "before-hook-end";
		if (name === "afterAll" || name === "afterEach") event = state === "run" ? "after-hook-start" : "after-hook-end";
		updateTask(event, task, runner);
	}
}
function getSuiteHooks(suite, name, sequence) {
	const hooks = getHooks(suite)[name];
	if (sequence === "stack" && (name === "afterAll" || name === "afterEach")) return hooks.slice().reverse();
	return hooks;
}
async function callTestHooks(runner, test, hooks, sequence) {
	if (sequence === "stack") hooks = hooks.slice().reverse();
	if (!hooks.length) return;
	const context = test.context;
	const onTestFailed = test.context.onTestFailed;
	const onTestFinished = test.context.onTestFinished;
	context.onTestFailed = () => {
		throw new Error(`Cannot call "onTestFailed" inside a test hook.`);
	};
	context.onTestFinished = () => {
		throw new Error(`Cannot call "onTestFinished" inside a test hook.`);
	};
	if (sequence === "parallel") try {
		await Promise.all(hooks.map((fn) => limitMaxConcurrency(() => fn(test.context))));
	} catch (e) {
		failTask(test.result, e, runner.config._diffOptions);
	}
	else for (const fn of hooks) try {
		await limitMaxConcurrency(() => fn(test.context));
	} catch (e) {
		failTask(test.result, e, runner.config._diffOptions);
	}
	context.onTestFailed = onTestFailed;
	context.onTestFinished = onTestFinished;
}
async function callSuiteHook(suite, currentTask, name, runner, args, cleanups = []) {
	const sequence = runner.config.sequence.hooks;
	const parentSuite = "filepath" in suite ? null : suite.suite || suite.file;
	if (name === "beforeEach" && parentSuite) await callSuiteHook(parentSuite, currentTask, name, runner, args, cleanups);
	const hooks = getSuiteHooks(suite, name, sequence);
	if (hooks.length > 0) updateSuiteHookState(currentTask, name, "run", runner);
	async function runHook(hook) {
		return limitMaxConcurrency(async () => {
			return getBeforeHookCleanupCallback(hook, await hook(...args), name === "beforeEach" ? args[0] : void 0);
		});
	}
	if (sequence === "parallel") {
		const results = await Promise.allSettled(hooks.map((hook) => runHook(hook)));
		const errors = /* @__PURE__ */ new Set();
		for (const result of results) if (result.status === "fulfilled") cleanups.push(result.value);
		else errors.add(result.reason);
		if (errors.size === 1) throw [...errors][0];
		if (errors.size > 1) throw new AggregateError([...errors], `Multiple "${name}" hooks failed.`);
	} else for (const hook of hooks) cleanups.push(await runHook(hook));
	if (hooks.length > 0) updateSuiteHookState(currentTask, name, "pass", runner);
	if (name === "afterEach" && parentSuite) await callSuiteHook(parentSuite, currentTask, name, runner, args);
}
function getAroundEachHooks(suite) {
	const hooks = [];
	const parentSuite = "filepath" in suite ? null : suite.suite || suite.file;
	if (parentSuite) hooks.push(...getAroundEachHooks(parentSuite));
	hooks.push(...getHooks(suite).aroundEach);
	return hooks;
}
function getAroundAllHooks(suite) {
	return getHooks(suite).aroundAll;
}
function makeAroundHookTimeoutError(hookName, phase, timeout, stackTraceError) {
	const message = `The ${phase} phase of "${hookName}" hook timed out after ${timeout}ms.`;
	const error = new (phase === "setup" ? AroundHookSetupError : AroundHookTeardownError)(message);
	if (stackTraceError?.stack) error.stack = stackTraceError.stack.replace(stackTraceError.message, error.message);
	return error;
}
async function callAroundHooks(runInner, options) {
	const { hooks, hookName, callbackName, onTimeout, invokeHook } = options;
	if (!hooks.length) {
		await runInner();
		return;
	}
	const hookErrors = [];
	const createTimeoutPromise = (timeout, phase, stackTraceError) => {
		let timer;
		let timedout = false;
		const promise = new Promise((_, reject) => {
			if (timeout > 0 && timeout !== Number.POSITIVE_INFINITY) {
				timer = setTimeout(() => {
					timedout = true;
					const error = makeAroundHookTimeoutError(hookName, phase, timeout, stackTraceError);
					onTimeout?.(error);
					reject(error);
				}, timeout);
				timer.unref?.();
			}
		});
		const clear = () => {
			if (timer) {
				clearTimeout(timer);
				timer = void 0;
			}
		};
		return {
			promise,
			clear,
			isTimedOut: () => timedout
		};
	};
	const runNextHook = async (index) => {
		if (index >= hooks.length) return runInner();
		const hook = hooks[index];
		const timeout = getAroundHookTimeout(hook);
		const stackTraceError = getAroundHookStackTrace(hook);
		let useCalled = false;
		let setupTimeout;
		let teardownTimeout;
		let setupLimitConcurrencyRelease;
		let teardownLimitConcurrencyRelease;
		let resolveUseCalled;
		const useCalledPromise = new Promise((resolve) => {
			resolveUseCalled = resolve;
		});
		let resolveUseReturned;
		const useReturnedPromise = new Promise((resolve) => {
			resolveUseReturned = resolve;
		});
		let resolveHookComplete;
		let rejectHookComplete;
		const hookCompletePromise = new Promise((resolve, reject) => {
			resolveHookComplete = resolve;
			rejectHookComplete = reject;
		});
		const use = async () => {
			if (setupTimeout.isTimedOut()) throw new Error("__VITEST_INTERNAL_AROUND_HOOK_ABORT__");
			if (useCalled) throw new AroundHookMultipleCallsError(`The \`${callbackName}\` callback was called multiple times in the \`${hookName}\` hook. The callback can only be called once per hook.`);
			useCalled = true;
			resolveUseCalled();
			setupTimeout.clear();
			setupLimitConcurrencyRelease?.();
			await runNextHook(index + 1).catch((e) => hookErrors.push(e));
			teardownLimitConcurrencyRelease = await limitMaxConcurrency.acquire();
			teardownTimeout = createTimeoutPromise(timeout, "teardown", stackTraceError);
			resolveUseReturned();
		};
		setupLimitConcurrencyRelease = await limitMaxConcurrency.acquire();
		setupTimeout = createTimeoutPromise(timeout, "setup", stackTraceError);
		(async () => {
			try {
				await invokeHook(hook, use);
				if (!useCalled) throw new AroundHookSetupError(`The \`${callbackName}\` callback was not called in the \`${hookName}\` hook. Make sure to call \`${callbackName}\` to run the ${hookName === "aroundEach" ? "test" : "suite"}.`);
				resolveHookComplete();
			} catch (error) {
				rejectHookComplete(error);
			} finally {
				setupLimitConcurrencyRelease?.();
				teardownLimitConcurrencyRelease?.();
			}
		})();
		try {
			await Promise.race([
				useCalledPromise,
				hookCompletePromise,
				setupTimeout.promise
			]);
		} finally {
			setupLimitConcurrencyRelease?.();
			setupTimeout.clear();
		}
		await Promise.race([useReturnedPromise, hookCompletePromise]);
		try {
			await Promise.race([hookCompletePromise, teardownTimeout?.promise]);
		} finally {
			teardownLimitConcurrencyRelease?.();
			teardownTimeout?.clear();
		}
	};
	await runNextHook(0).catch((e) => hookErrors.push(e));
	if (hookErrors.length > 0) throw hookErrors;
}
async function callAroundAllHooks(suite, runSuiteInner) {
	await callAroundHooks(runSuiteInner, {
		hooks: getAroundAllHooks(suite),
		hookName: "aroundAll",
		callbackName: "runSuite()",
		invokeHook: (hook, use) => hook(use, suite)
	});
}
async function callAroundEachHooks(suite, test, runTest) {
	await callAroundHooks(() => runTest(getFixtureCleanupCount(test.context)), {
		hooks: getAroundEachHooks(suite),
		hookName: "aroundEach",
		callbackName: "runTest()",
		onTimeout: (error) => abortContextSignal(test.context, error),
		invokeHook: (hook, use) => hook(use, test.context, suite)
	});
}
const packs = /* @__PURE__ */ new Map();
const eventsPacks = [];
const pendingTasksUpdates = [];
function sendTasksUpdate(runner) {
	if (packs.size) {
		const taskPacks = Array.from(packs).map(([id, task]) => {
			return [
				id,
				task[0],
				task[1]
			];
		});
		const p = runner.onTaskUpdate?.(taskPacks, eventsPacks);
		if (p) {
			pendingTasksUpdates.push(p);
			p.then(() => pendingTasksUpdates.splice(pendingTasksUpdates.indexOf(p), 1), () => {});
		}
		eventsPacks.length = 0;
		packs.clear();
	}
}
async function finishSendTasksUpdate(runner) {
	sendTasksUpdate(runner);
	await Promise.all(pendingTasksUpdates);
}
function throttle(fn, ms) {
	let last = 0;
	let pendingCall;
	return function call(...args) {
		const now = unixNow();
		if (now - last > ms) {
			last = now;
			clearTimeout(pendingCall);
			pendingCall = void 0;
			return fn.apply(this, args);
		}
		pendingCall ??= setTimeout(() => {
			pendingCall = void 0;
			call.apply(this, args);
		}, ms);
	};
}
const sendTasksUpdateThrottled = throttle(sendTasksUpdate, 100);
function updateTask(event, task, runner) {
	eventsPacks.push([
		task.id,
		event,
		void 0
	]);
	packs.set(task.id, [task.result, task.meta]);
	sendTasksUpdateThrottled(runner);
}
async function callCleanupHooks(runner, cleanups) {
	const sequence = runner.config.sequence.hooks;
	if (sequence === "stack") cleanups = cleanups.slice().reverse();
	if (sequence === "parallel") await Promise.all(cleanups.map(async (fn) => {
		if (typeof fn !== "function") return;
		await limitMaxConcurrency(() => fn());
	}));
	else for (const fn of cleanups) {
		if (typeof fn !== "function") continue;
		await limitMaxConcurrency(() => fn());
	}
}
/**
* Determines if a test should be retried based on its retryCondition configuration
*/
function passesRetryCondition(test, errors) {
	const condition = getRetryCondition(test.retry);
	const error = errors?.at(-1);
	if (error == null) return false;
	if (!condition) return true;
	if (condition instanceof RegExp) return condition.test(error.message || "");
	else if (typeof condition === "function") return condition(error);
	return false;
}
async function runTest(test, runner) {
	await runner.onBeforeRunTask?.(test);
	if (test.mode !== "run" && test.mode !== "queued") {
		updateTask("test-prepare", test, runner);
		updateTask("test-finished", test, runner);
		return;
	}
	if (test.result?.state === "fail") {
		updateTask("test-failed-early", test, runner);
		return;
	}
	const start = now();
	test.result = {
		state: "run",
		startTime: unixNow(),
		retryCount: 0,
		attempts: []
	};
	updateTask("test-prepare", test, runner);
	const cleanupRunningTest = addRunningTest(test);
	setCurrentTest(test);
	const suite = test.suite || test.file;
	const $ = runner.trace;
	const repeats = test.repeats ?? 0;
	let hasFailedRepeat = false;
	for (let repeatCount = 0; repeatCount <= repeats; repeatCount++) {
		test.result.state = "run";
		const retry = getRetryCount(test.retry);
		for (let retryCount = 0; retryCount <= retry; retryCount++) {
			const attemptStart = now();
			const attemptStartTime = unixNow();
			const attemptErrorsStart = test.result.errors?.length ?? 0;
			const recordAttempt = (state) => {
				const errors = test.result.errors?.slice(attemptErrorsStart);
				test.result.attempts.push({
					state,
					errors: errors?.length ? errors : void 0,
					duration: now() - attemptStart,
					startTime: attemptStartTime,
					retryIndex: retryCount,
					repeatIndex: repeatCount
				});
			};
			const beforeEachCleanups = [];
			await callAroundEachHooks(suite, test, async (fixtureCheckpoint) => {
				try {
					await runner.onBeforeTryTask?.(test, {
						retry: retryCount,
						repeats: repeatCount
					});
					test.result.repeatCount = repeatCount;
					await $("test.beforeEach", () => callSuiteHook(suite, test, "beforeEach", runner, [test.context, suite], beforeEachCleanups));
					if (runner.runTask) await $("test.callback", () => limitMaxConcurrency(() => runner.runTask(test)));
					else {
						const fn = getFn(test);
						if (!fn) throw new Error("Test function is not found. Did you add it using `setFn`?");
						await $("test.callback", () => limitMaxConcurrency(() => fn()));
					}
					await runner.onAfterTryTask?.(test, {
						retry: retryCount,
						repeats: repeatCount
					});
					if (test.result.state !== "fail") test.result.state = "pass";
				} catch (e) {
					failTask(test.result, e, runner.config._diffOptions);
				}
				try {
					await runner.onTaskFinished?.(test);
				} catch (e) {
					failTask(test.result, e, runner.config._diffOptions);
				}
				try {
					await $("test.afterEach", () => callSuiteHook(suite, test, "afterEach", runner, [test.context, suite]));
					if (beforeEachCleanups.length) await $("test.cleanup", () => callCleanupHooks(runner, beforeEachCleanups));
					await callFixtureCleanupFrom(test.context, fixtureCheckpoint);
				} catch (e) {
					failTask(test.result, e, runner.config._diffOptions);
				}
				if (test.onFinished?.length) await $("test.onFinished", () => callTestHooks(runner, test, test.onFinished, "stack"));
				if (test.result.state === "fail" && test.onFailed?.length) await $("test.onFailed", () => callTestHooks(runner, test, test.onFailed, runner.config.sequence.hooks));
				test.onFailed = void 0;
				test.onFinished = void 0;
				await runner.onAfterRetryTask?.(test, {
					retry: retryCount,
					repeats: repeatCount
				});
			}).catch((error) => {
				failTask(test.result, error, runner.config._diffOptions);
			});
			try {
				await callFixtureCleanup(test.context);
			} catch (e) {
				failTask(test.result, e, runner.config._diffOptions);
			}
			if (test.result?.pending || test.result?.state === "skip") {
				recordAttempt("skip");
				test.mode = "skip";
				test.result = {
					state: "skip",
					note: test.result?.note,
					pending: true,
					duration: now() - start,
					attempts: test.result.attempts
				};
				updateTask("test-finished", test, runner);
				setCurrentTest(void 0);
				cleanupRunningTest();
				return;
			}
			if (test.fails) {
				if (test.result.state === "pass") {
					const error = processError(/* @__PURE__ */ new Error("Expect test to fail"));
					test.result.state = "fail";
					test.result.errors ??= [];
					test.result.errors.push(error);
				} else if (!test.result.errors?.slice(attemptErrorsStart).some((e) => e.__vitest_test_syntax_error__)) {
					test.result.state = "pass";
					test.result.errors?.splice(attemptErrorsStart);
					if (!test.result.errors?.length) test.result.errors = void 0;
				}
			}
			recordAttempt(test.result.state === "pass" ? "pass" : "fail");
			if (test.result.state === "pass") break;
			if (retryCount < retry) {
				if (!passesRetryCondition(test, test.result.errors)) break;
				test.result.state = "run";
				test.result.retryCount = (test.result.retryCount ?? 0) + 1;
				const delay = getRetryDelay(test.retry);
				if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay));
			}
			updateTask("test-retried", test, runner);
		}
		if (test.result.state === "fail") hasFailedRepeat = true;
	}
	if (hasFailedRepeat) test.result.state = "fail";
	cleanupRunningTest();
	setCurrentTest(void 0);
	test.result.duration = now() - start;
	await runner.onAfterRunTask?.(test);
	updateTask("test-finished", test, runner);
}
function failTask(result, err, diffOptions) {
	if (err instanceof PendingError) {
		result.state = "skip";
		result.note = err.note;
		result.pending = true;
		return;
	}
	if (err instanceof TestRunAbortError) {
		result.state = "skip";
		result.note = err.message;
		return;
	}
	result.state = "fail";
	const errors = Array.isArray(err) ? err : [err];
	for (const e of errors) {
		const errors = e instanceof AggregateError ? e.errors.map((e) => processError(e, diffOptions)) : [processError(e, diffOptions)];
		result.errors ??= [];
		result.errors.push(...errors);
	}
}
function markTasksAsSkipped(suite, runner) {
	suite.tasks.forEach((t) => {
		t.mode = "skip";
		t.result = {
			...t.result,
			state: "skip"
		};
		updateTask("test-finished", t, runner);
		if (t.type === "suite") markTasksAsSkipped(t, runner);
	});
}
function markPendingTasksAsSkipped(suite, runner, note) {
	suite.tasks.forEach((t) => {
		if (!t.result || t.result.state === "run") {
			t.mode = "skip";
			t.result = {
				...t.result,
				state: "skip",
				note
			};
			updateTask("test-cancel", t, runner);
		}
		if (t.type === "suite") markPendingTasksAsSkipped(t, runner, note);
	});
}
async function runSuite(suite, runner) {
	await runner.onBeforeRunSuite?.(suite);
	if (suite.result?.state === "fail") {
		markTasksAsSkipped(suite, runner);
		updateTask("suite-failed-early", suite, runner);
		return;
	}
	const start = now();
	const mode = suite.mode;
	suite.result = {
		state: mode === "skip" || mode === "todo" ? mode : "run",
		startTime: unixNow()
	};
	const $ = runner.trace;
	updateTask("suite-prepare", suite, runner);
	const beforeAllCleanups = [];
	if (suite.mode === "skip") {
		suite.result.state = "skip";
		updateTask("suite-finished", suite, runner);
	} else if (suite.mode === "todo") {
		suite.result.state = "todo";
		updateTask("suite-finished", suite, runner);
	} else {
		let suiteRan = false;
		try {
			await callAroundAllHooks(suite, async () => {
				suiteRan = true;
				try {
					try {
						await $("suite.beforeAll", () => callSuiteHook(suite, suite, "beforeAll", runner, [suite], beforeAllCleanups));
					} catch (e) {
						failTask(suite.result, e, runner.config._diffOptions);
						markTasksAsSkipped(suite, runner);
						return;
					}
					if (runner.runSuite) await runner.runSuite(suite);
					else for (let tasksGroup of partitionSuiteChildren(suite)) if (tasksGroup[0].concurrent === true) {
						const groupLimiter = limitConcurrency(runner.config.maxConcurrency);
						await Promise.all(tasksGroup.map((c) => groupLimiter(() => runSuiteChild(c, runner))));
					} else {
						const { sequence } = runner.config;
						if (suite.shuffle) tasksGroup = shuffle([tasksGroup.filter((group) => group.type === "suite"), tasksGroup.filter((group) => group.type === "test")], sequence.seed).flatMap((group) => shuffle(group, sequence.seed));
						for (const c of tasksGroup) await runSuiteChild(c, runner);
					}
				} finally {
					try {
						await $("suite.afterAll", () => callSuiteHook(suite, suite, "afterAll", runner, [suite]));
						if (beforeAllCleanups.length) await $("suite.cleanup", () => callCleanupHooks(runner, beforeAllCleanups));
						if (suite.file === suite) {
							const contexts = TestFixtures.getFileContexts(suite.file);
							await Promise.all(contexts.map((context) => callFixtureCleanup(context)));
						}
					} catch (e) {
						failTask(suite.result, e, runner.config._diffOptions);
					}
				}
			});
		} catch (e) {
			if (!suiteRan) markTasksAsSkipped(suite, runner);
			failTask(suite.result, e, runner.config._diffOptions);
		}
		if (suite.mode === "run" || suite.mode === "queued") {
			if (!runner.config.passWithNoTests && !suite.containsTest) {
				suite.result.state = "fail";
				if (!suite.result.errors?.length) {
					const error = processError(/* @__PURE__ */ new Error(`No test found in suite ${suite.name}`));
					suite.result.errors = [error];
				}
			} else if (hasFailed(suite)) suite.result.state = "fail";
			else suite.result.state = "pass";
		}
		suite.result.duration = now() - start;
		await runner.onAfterRunSuite?.(suite);
		updateTask("suite-finished", suite, runner);
	}
}
async function runSuiteChild(c, runner) {
	const $ = runner.trace;
	if (c.type === "test") return $("run.test", {
		"vitest.test.id": c.id,
		"vitest.test.name": c.name,
		"vitest.test.mode": c.mode,
		"vitest.test.timeout": c.timeout,
		"code.file.path": c.file.filepath,
		"code.line.number": c.location?.line,
		"code.column.number": c.location?.column
	}, () => limitTestConcurrency(() => runTest(c, runner)));
	else if (c.type === "suite") return $("run.suite", {
		"vitest.suite.id": c.id,
		"vitest.suite.name": c.name,
		"vitest.suite.mode": c.mode,
		"code.file.path": c.file.filepath,
		"code.line.number": c.location?.line,
		"code.column.number": c.location?.column
	}, () => runSuite(c, runner));
}
async function runFiles(files, runner) {
	limitMaxConcurrency ??= limitConcurrency(runner.config.maxConcurrency);
	limitTestConcurrency ??= limitConcurrency(runner.config.maxConcurrency);
	for (const file of files) {
		if (!file.tasks.length && !runner.config.passWithNoTests) {
			if (!file.result?.errors?.length) file.result = {
				state: "fail",
				errors: [processError(/* @__PURE__ */ new Error(`No test suite found in file ${file.filepath}`))]
			};
		}
		await runner.trace("run.spec", {
			"code.file.path": file.filepath,
			"vitest.suite.tasks.length": file.tasks.length
		}, () => runSuite(file, runner));
	}
}
const workerRunners = /* @__PURE__ */ new WeakSet();
function defaultTrace(_, attributes, cb) {
	if (typeof attributes === "function") return attributes();
	return cb();
}
async function startTests(specs, runner) {
	runner.trace ??= defaultTrace;
	const cancel = runner.cancel?.bind(runner);
	runner.cancel = (reason) => {
		const error = new TestRunAbortError("The test run was aborted by the user.", reason);
		getRunningTests().forEach((test) => {
			abortContextSignal(test.context, error);
			markPendingTasksAsSkipped(test.file, runner, error.message);
		});
		return cancel?.(reason);
	};
	if (!workerRunners.has(runner)) {
		runner.onCleanupWorkerContext?.(async () => {
			await Promise.all(Array.from(TestFixtures.getWorkerContexts(), (context) => callFixtureCleanup(context))).finally(() => {
				TestFixtures.clearDefinitions();
			});
		});
		workerRunners.add(runner);
	}
	try {
		const paths = specs.map((f) => typeof f === "string" ? f : f.filepath);
		await runner.onBeforeCollect?.(paths);
		const files = await collectTests(specs, runner);
		await runner.onCollected?.(files);
		await runner.onBeforeRunFiles?.(files);
		await runFiles(files, runner);
		await runner.onAfterRunFiles?.(files);
		await finishSendTasksUpdate(runner);
		return files;
	} finally {
		runner.cancel = cancel;
	}
}
async function publicCollect(specs, runner) {
	runner.trace ??= defaultTrace;
	const paths = specs.map((f) => typeof f === "string" ? f : f.filepath);
	await runner.onBeforeCollect?.(paths);
	const files = await collectTests(specs, runner);
	await runner.onCollected?.(files);
	return files;
}
// node_modules/vitest/dist/chunks/base.Cof-UHJf.js
let globalSetup = false;
async function setupGlobalEnv(config, environment) {
	await s$5(config);
	Object.defineProperty(globalThis, "__vitest_index__", {
		value: i,
		enumerable: false
	});
	g$1.setState({ environment: environment.name });
	if (globalSetup) return;
	globalSetup = true;
	if ((environment.viteEnvironment || environment.name) === "client") {
		const _require = createRequire("file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/base.Cof-UHJf.js");
		_require.extensions[".css"] = resolveCss;
		_require.extensions[".scss"] = resolveCss;
		_require.extensions[".sass"] = resolveCss;
		_require.extensions[".less"] = resolveCss;
		K.forEach((type) => {
			_require.extensions[`.${type}`] = resolveAsset;
		});
		process.env.SSR = "";
	} else process.env.SSR = "1";
	globalThis.__vitest_required__ = {
		util,
		timers,
		timersPromises,
		syncBuiltinESMExports
	};
	if (!config.disableConsoleIntercept) await setupConsoleLogSpy();
}
function resolveCss(mod) {
	mod.exports = "";
}
function resolveAsset(mod, url) {
	mod.exports = url;
}
async function setupConsoleLogSpy() {
	const { createCustomConsole } = await import("file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/console.CQZ877Hi.js");
	globalThis.console = createCustomConsole();
}
async function run(method, files, config, moduleRunner, environment, traces) {
	const workerState = g$3();
	const [testRunner] = await Promise.all([
		traces.$("vitest.runtime.runner", () => r(config, moduleRunner, traces)),
		traces.$("vitest.runtime.global_env", () => setupGlobalEnv(config, environment)),
		traces.$("vitest.runtime.coverage.start", () => s$3(config.coverage, moduleRunner, { isolate: config.isolate })),
		traces.$("vitest.runtime.snapshot.environment", async () => {
			if (!workerState.config.snapshotOptions.snapshotEnvironment) workerState.config.snapshotOptions.snapshotEnvironment = await a(config, moduleRunner);
		})
	]);
	const offCancel = workerState.onCancel((reason) => {
		c$2(config);
		testRunner.cancel?.(reason);
	});
	workerState.durations.prepare = performance$1.now() - workerState.durations.prepare;
	const dependencies = workerState.dependencies;
	const preparedModules = new Set(dependencies);
	const getImports = config.experimental.viteModuleRunner === false ? n.getImports : g(workerState.evaluatedModules);
	try {
		await traces.$(`vitest.test.runner.${method}`, async () => {
			for (const file of files) {
				if (config.isolate) {
					moduleRunner.mocker?.reset();
					r$1(workerState.evaluatedModules, true);
				}
				workerState.filepath = file.filepath;
				dependencies?.clear();
				if (method === "run") {
					const collectAsyncLeaks = config.detectAsyncLeaks ? d(file.filepath, workerState.ctx.projectName) : void 0;
					await traces.$(`vitest.test.runner.${method}.module`, { attributes: { "code.file.path": file.filepath } }, () => startTests([file], testRunner));
					if (dependencies) workerState.rpc.onTestModuleDependencies(file.filepath, c(getImports, preparedModules, dependencies));
					const leaks = await collectAsyncLeaks?.();
					if (leaks?.length) workerState.rpc.onAsyncLeaks(leaks);
				} else await traces.$(`vitest.test.runner.${method}.module`, { attributes: { "code.file.path": file.filepath } }, () => publicCollect([file], testRunner));
				v.resetConfig();
				v.restoreAllMocks();
				moduleRunner.mocker?.reset();
			}
		});
	} finally {
		offCancel();
	}
	await traces.$("vitest.runtime.coverage.stop", () => a$2(config.coverage, moduleRunner, { isolate: config.isolate }));
}
let _moduleRunner;
const evaluatedModules = new V();
const moduleExecutionInfo = /* @__PURE__ */ new Map();
async function startModuleRunner(options) {
	if (_moduleRunner) return _moduleRunner;
	const state = () => a$5() || options.state;
	process.exit = (code = process.exitCode || 0) => {
		const filepath = state().filepath;
		throw new Error(`process.exit unexpectedly called with "${code}"${filepath ? ` (test file: ${filepath})` : ""}`);
	};
	a$1(state);
	if (options.state.config.experimental.viteModuleRunner === false) {
		const root = options.state.config.root;
		let mocker;
		if (options.state.config.experimental.nodeLoader !== false) {
			const { NativeModuleMocker } = await import("file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/nativeModuleMocker.Dh5azSB4.js");
			mocker = new NativeModuleMocker({
				async resolveId(id, importer) {
					return state().rpc.resolve(id, importer, "__vitest__");
				},
				root,
				moduleDirectories: state().config.deps.moduleDirectories || ["/node_modules/"],
				traces: options.traces || new Traces({ enabled: false }),
				getCurrentTestFilepath() {
					return state().filepath;
				},
				spyModule: s
			});
		}
		const { NativeModuleRunner } = await import("file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/nativeModuleRunner.9xPIMzi7.js").then(function(n) {
			return n.n;
		});
		_moduleRunner = new NativeModuleRunner(root, mocker);
		return _moduleRunner;
	}
	_moduleRunner = s$2(options);
	return _moduleRunner;
}
let _currentEnvironment;
let _environmentModules = [];
let _environmentTime;
let _nativeDependencies;
let _canRecordDependencies = true;
function recordNativeImport(url, parentURL) {
	const file = n.record(url, parentURL);
	if (file) _nativeDependencies?.add(file);
}
/** @experimental */
async function setupBaseEnvironment(context) {
	if (context.config.experimental.viteModuleRunner === false) {
		const { setupNodeLoaderHooks } = await import("file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/native.upB-V0CK.js");
		const recordDependencies = context.config.experimental.recordDependencies;
		_canRecordDependencies = await setupNodeLoaderHooks(context, recordDependencies ? recordNativeImport : void 0);
		_nativeDependencies = recordDependencies ? /* @__PURE__ */ new Set() : void 0;
	}
	const startTime = performance.now();
	const { environment: { name: environmentName, options: environmentOptions }, rpc, config } = context;
	b$1(config.env, context.metaEnv);
	const otel = context.traces;
	const { environment, loader } = await l(environmentName, config.root, rpc, otel, context.config.experimental.viteModuleRunner);
	_currentEnvironment = environment;
	_environmentModules = loader ? Array.from(loader.evaluatedModules.idToModuleMap.keys()) : _nativeDependencies ?? [];
	const env = await otel.$("vitest.runtime.environment.setup", { attributes: {
		"vitest.environment": environment.name,
		"vitest.environment.vite_environment": environment.viteEnvironment || environment.name
	} }, () => environment.setup(globalThis, environmentOptions || config.environmentOptions || {}));
	_environmentTime = performance.now() - startTime;
	if (config.chaiConfig) s$1(config.chaiConfig);
	return async () => {
		await otel.$("vitest.runtime.environment.teardown", () => env.teardown(globalThis));
		await loader?.close();
	};
}
/** @experimental */
async function runBaseTests(method, state, traces) {
	const { ctx } = state;
	state.environment = _currentEnvironment;
	state.durations.environment = _environmentTime;
	state.evaluatedModules = evaluatedModules;
	state.moduleExecutionInfo = moduleExecutionInfo;
	if (!_canRecordDependencies) state.dependencies = void 0;
	for (const id of _environmentModules) state.dependencies?.add(id);
	if (_nativeDependencies) _nativeDependencies = state.dependencies;
	p$2(globalThis, state);
	if (state.config.serializedDefines) try {
		runInThisContext(`(() =>{\n${state.config.serializedDefines}})()`, {
			lineOffset: 1,
			filename: "virtual:load-defines.js"
		});
	} catch (error) {
		throw new Error(`Failed to load custom "defines": ${error.message}`);
	}
	ctx.files.forEach((i) => {
		const filepath = i.filepath;
		(state.evaluatedModules.fileToModulesMap.get(filepath) || []).forEach((module) => {
			state.evaluatedModules.invalidateModule(module);
		});
	});
	const moduleRunner = await startModuleRunner({
		state,
		evaluatedModules: state.evaluatedModules,
		spyModule: s,
		createImportMeta: c$1,
		traces
	});
	e(moduleRunner);
	await run(method, ctx.files, ctx.config, moduleRunner, _currentEnvironment, traces);
}
// node_modules/vitest/dist/workers/forks.js
w$1({
	runTests: runBaseTests,
	setup: setupBaseEnvironment
});
export {};

//# sourceMappingURL=negative-worker.mjs.map