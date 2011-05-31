#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (c) 2001-2010 Chris Liechti <cliechti@gmx.net>
# All Rights Reserved.
# Simplified BSD License (see LICENSE.txt for full text)

"""\
Simple C preprocessor (almost). It is not fully compliant to a real
ANSI C preprocessor, but it understands a helpful subset.
"""

# TODO:
# - stringify #
# - it isn't fully compatible to a real cpp

import os
import re
import logging
import codecs
from msp430.asm import infix2postfix
from msp430.asm import rpn


def line_joiner(next_line):
    """\
    Given a readline function, return lines, but handle line continuations
    ('\\\n'). When lines are joined, the same number of blank lines is output
    so that the line counter for the consumer stays correct.
    """
    while True:
        joined_line = '\\\n'
        joined_lines = 0
        while joined_line[-2:] == '\\\n':
            joined_line = joined_line[:-2]
            line = next_line()
            if not line: break
            joined_line += line.rstrip() + '\n' # XXX drops spaces too
            joined_lines += 1
        while joined_lines > 1:
            yield '\n'
            joined_lines -= 1
        if not joined_line: break
        yield joined_line


class PreprocessorError(Exception):
    """Preprocessor specific errors"""


class Scanner(infix2postfix.Scanner):
    scannerRE = re.compile(r'''
       (?P<SPACE>           \s+             ) |
       (?P<LEFT>            \(              ) |
       (?P<RIGHT>           \)              ) |
       (?P<HEXNUMBER>       0x[0-9a-f]+     ) |
       (?P<BINNUMBER>       0b[0-9a-f]+     ) |
       (?P<NUMBER>          \d+             ) |
       (?P<UNARYOPERATOR>   defined|!|(\B[-+~]\b)   ) |
       (?P<OPERATOR>        \|\||&&|<<|>>|==|!=|<=|>=|[-+*/\|&\^<>] ) |
       (?P<VARIABLE>        \.?[$_a-z]\w*   )
    ''', re.VERBOSE|re.IGNORECASE|re.UNICODE)

cpp_precedence_list = [
        # lowest precedence
        ['||', 'or'],
        ['&&', 'and'],
        ['|', '^', '&'],
        ['==', '!='],
        ['<', '<=', '>', '>='],
        ['<<', '>>'],
        ['+', '-'],
        ['*', '/', '%'],
        ['!', 'not'],
        ['~', 'neg', '0 +'],
        ['defined'],
        ['(', ')'],
        # highest precedence
        ]

precedence = infix2postfix.convert_precedence_list(cpp_precedence_list)

class Undefined(object):
    def __int__(self): return 0
    def __str__(self): return ''
undefined = Undefined()


class Evaluator(rpn.RPN):
    """\
    An RPN calculator with infix to postfix converter, so that expressions for
    #if can be evaluated.
    """
    def __init__(self):
        rpn.RPN.__init__(self)
        self.defines = {}
        # add some aliases for logic operators
        self.builtins['!'] = self.builtins['not']
        self.builtins['&&'] = self.builtins['and']
        self.builtins['||'] = self.builtins['or']

    @rpn.word('LOOKUP')
    def word_LOOKUP(self, stack):
        key = self.next_word()
        if key in self.defines:
            self.push(self.defines[key])
        else:
            self.push(undefined)

    @rpn.word('DEFINED')
    def word_DEFINED(self, stack):
        self.push(self.pop() is not undefined)  #XXX

    def eval(self, expression):
        self.clear()
        try:
            rpn_expr = infix2postfix.infix2postfix(
                    expression,
                    scanner=Scanner,
                    precedence = precedence,
                    variable_prefix='LOOKUP ')
        except ValueError, e:
            raise PreprocessorError('error in expression: %r' % (expression,))
        #~ print >>sys.stderr, "RPN: ", rpn_expr # XXX debug
        self.interpret_sequence(rpn_expr.split(' '))
        if len(self) != 1:
            raise PreprocessorError('error in expression: %r stack: %s' % (expression, self))
        return self.pop()


class AnnoatatedLineWriter(object):
    """\
    Write lines to the output. If line numbers jump also write out an
    information line with line number and filename: '# nn "filename"'
    """
    def __init__(self, output, filename):
        self.output = output
        self.filename = filename
        self.marker = None

    def write(self, lineno, text):
        """
        Write line. It is expected to be called with lines only (ending in
        '\n') otherwise will the markers be missplaced.
        """
        # emit line number and file hints for the next stage
        if self.marker != lineno:
            self.output.write('# %d "%s"\n' % (lineno, self.filename))
        self.marker = lineno+1
        try:
            self.output.write(text)
        except IOError:
            raise EOFError()


class Preprocessor(object):
    """\
    A text processing tool like a C Preprocessor. However it is not 100%
    compatible to a C Preprocessor.
    """

    re_scanner = re.compile(r'''
            (?P<MACRO>      ^[\t ]*\#[\t ]*define[\t ]+(?P<MACRO_NAME>\w+)\((?P<MACRO_ARGS>.*?)\)(?P<MACRO_DEF>[\t ]+(.+))? ) |
            (?P<DEFINE>     ^[\t ]*\#[\t ]*define[\t ]+(?P<DEF_NAME>\w+)([\t ]+)?(?P<DEF_VALUE>.+)?     ) |
            (?P<INCLUDE>    ^[\t ]*\#[\t ]*include[\t ]+[<"](?P<INC_NAME>[\w\\/\.]+)[">]  ) |
            (?P<IF>         ^[\t ]*\#[\t ]*if[\t ]+(?P<IF_EXPR>.*)       ) |
            (?P<IFDEF>      ^[\t ]*\#[\t ]*ifdef[\t ]+(?P<IFDEF_NAME>.*)    ) |
            (?P<IFNDEF>     ^[\t ]*\#[\t ]*ifndef[\t ]+(?P<IFNDEF_NAME>.*)   ) |
            (?P<ELSE>       ^[\t ]*\#[\t ]*else               ) |
            (?P<ENDIF>      ^[\t ]*\#[\t ]*endif              ) |
            (?P<UNDEF>      ^[\t ]*\#[\t ]*undef[\t ]+(?P<UNDEF_NAME>.*)    ) |
            (?P<NONPREPROC> ^[^\#].*         )
            ''', re.VERBOSE|re.UNICODE)

    re_silinecomment = re.compile('(//).*')
    re_inlinecomment = re.compile('/\*.*?\*/')
    re_splitter = re.compile('(\W+)', re.UNICODE)
    re_macro_usage = re.compile('(?P<MACRO>\w+)[\t ]*\((?P<ARGS>.*)\)', re.UNICODE)

    def __init__(self):
        self.macros = {}
        self.log = logging.getLogger('cpp')
        self.namespace = Evaluator()
        self.include_path = ['.']

    def _apply_macro(self, match_obj):
        name = match_obj.group('MACRO')
        if name in self.macros:
            values = [x.strip() for x in match_obj.group('ARGS').split(',')]
            args, expansion = self.macros[name]
            if len(args) != len(values):
                raise PreprocessorError('Macro invocation with wrong number of parameters. Expected %d got %d' % (
                        len(args), len(values)))
            self.expansion_done = True
            return expansion % dict(zip(args, values))
        else:
            return match_obj.group(0)   # nothing of ours, return original


    def expand(self, line):
        """Expand object and function like macros in given line."""
        recusion_limit = 10
        self.expansion_done = True
        while self.expansion_done and recusion_limit:
            # replace function like macros
            self.expansion_done = False
            line = self.re_macro_usage.sub(self._apply_macro, line)
            # replace object like macros
            words = self.re_splitter.split(line)
            res = []
            #print words         #DEBUG
            for word in words:
                if word in self.namespace.defines:
                    res.append(self.namespace.defines[word])
                    self.expansion_done = True
                #~ elif word in self.macros:
                    #~ expansion_done = True
                else:
                    res.append(word)
            line = ''.join(res)
            recusion_limit -= 1
        if recusion_limit == 0:
            self.log.error('recursive define, stopped expansion: %r ' % ' '.join(words))
        #~ print "expand -> %r" % (res)          #DEBUG
        return line.replace('##', '')

    def preprocess(self, infile, outfile, filename):
        """Scan lines and process preprocessor directives"""
        self.log.info("processing %s" % filename)
        empty_lines = 0
        process = True
        my_if_was_not_hidden = False
        if_name = '<???>'
        hiddenstack = []
        in_comment = False
        writer = AnnoatatedLineWriter(outfile, filename)
        line = ''
        lineno = 0
        try:
            for line in line_joiner(iter(infile).next):
                lineno += 1
                #~ print "|", line.rstrip()
                line = self.re_inlinecomment.sub('', line) #.strip()
                if in_comment:
                    p = line.find('*/')
                    if p >= 0:
                        in_comment = False
                        line = line[p+2:]
                        if not line.strip():
                            continue
                    else:
                        continue
                #~ line = line.rstrip()
                line = self.re_silinecomment.sub('', line)
                #~ print ">", line
                if not in_comment:
                    p = line.find('/*')
                    if p >= 0:
                        in_comment = True
                        line = line[:p]
                        if not line.strip():
                            continue
                #~ if not line.strip(): continue

                m = self.re_scanner.match(line)
                if m is None:
                    raise PreprocessorError("no match, internal error: %r" % line)
                elif m.lastgroup == 'IF':
                    expression = m.group('IF_EXPR')
                    value = self.namespace.eval(expression)
                    self.log.debug("#if %s -> %r" % (expression, value))
                    hiddenstack.append((process, my_if_was_not_hidden, if_name))
                    if_name = expression
                    if process:
                        process = value
                        my_if_was_not_hidden = True
                    else:
                        my_if_was_not_hidden = False
                    continue
                elif m.lastgroup == 'IFDEF':
                    symbol = m.group('IFDEF_NAME')
                    value = self.namespace.defines.has_key(symbol)
                    self.log.debug("#ifdef %s -> %r" % (symbol, value))
                    hiddenstack.append((process, my_if_was_not_hidden, if_name))
                    if_name = symbol
                    if process:
                        process = value
                        my_if_was_not_hidden = True
                    else:
                        my_if_was_not_hidden = False
                    continue
                elif m.lastgroup == 'IFNDEF':
                    symbol = m.group('IFNDEF_NAME')
                    value = not self.namespace.defines.has_key(symbol)
                    self.log.debug("#ifndef %s -> %r" % (symbol, value))
                    hiddenstack.append((process, my_if_was_not_hidden, if_name))
                    if_name = symbol
                    if process:
                        process = value
                        my_if_was_not_hidden = True
                    else:
                        my_if_was_not_hidden = False
                    continue
                elif m.lastgroup == 'ELSE':
                    self.log.debug("#else %s" % (if_name,))
                    if my_if_was_not_hidden:
                        process = not process
                    continue
                elif m.lastgroup == 'ENDIF':
                    self.log.debug("#endif %s" % (if_name,))
                    (process, my_if_was_not_hidden, if_name) = hiddenstack.pop()
                    continue
                elif not process:
                    continue
                elif m.lastgroup == 'INCLUDE':
                    include_name = m.group('INC_NAME')
                    self.log.debug('including "%s"' % (include_name,))
                    for location in self.include_path:
                        path = os.path.normpath(os.path.join(location, include_name))
                        if os.path.exists(path):
                            self.preprocess(codecs.open(path, 'r', 'utf-8'), outfile, path)
                            writer.marker = None  # force marker output
                            break
                    else:
                        raise PreprocessorError('include file %r not found' % (include_name,))
                    continue
                elif m.lastgroup == 'MACRO':
                    name = m.group('MACRO_NAME')
                    args = [x.strip() for x in m.group('MACRO_ARGS').split(',')]
                    if m.group('MACRO_DEF'):
                        definition = m.group('MACRO_DEF').strip()
                    else:
                        definition = ''
                    if self.macros.has_key(name):
                        self.log.warn("%r redefinition ignored" % (name),)
                    else:
                        # prepare the macro value to be used as format string
                        # (python's % operator)
                        definition = definition.replace('%', '%%')
                        for arg in args:
                            definition = re.sub(
                                    r'(^|[^\w_])#(%s)([^\w_]|$)' % arg,
                                    r'\1"%%(%s)s"\3' % arg.encode('hex'),
                                    definition)
                            definition = re.sub(
                                    r'(^|[^\w_])(%s)([^\w_]|$)' % arg,
                                    r'\1%%(%s)s\3' % arg.encode('hex'),
                                    definition)
                        self.macros[name] = ([x.encode('hex') for x in args], definition)
                        self.log.debug("defined macro %r => %r" % (name, self.macros[name]))
                    continue
                elif m.lastgroup == 'DEFINE':
                    symbol = m.group('DEF_NAME')
                    if m.group('DEF_VALUE'):
                        definition = m.group('DEF_VALUE').strip()
                    else:
                        definition = ''
                    if self.namespace.defines.has_key(symbol):
                        self.log.warn("%r redefinition ignored" % (symbol,))
                    else:
                        self.namespace.defines[symbol] = definition
                        self.log.debug("defined %r => %r" % (symbol, self.namespace.defines[symbol]))
                    continue
                elif m.lastgroup == 'UNDEF':
                    symbol = m.group('UNDEF_NAME')
                    self.log.debug("undefined %s" % (symbol,))
                    del self.namespace.defines[symbol]
                    continue
                elif m.lastgroup == 'NONPREPROC':
                    line = self.expand(line)
                else:
                    raise PreprocessorError('Invalid input: %r' % (line,))

                if not line.strip():
                    empty_lines += 1
                    if empty_lines > 1:
                        continue
                else:
                    empty_lines = 0
                writer.write(lineno, line)
            # at the end of the loop, check if there were unbalanced #if's
            if hiddenstack:
                raise PreprocessorError('missing #endif')

        except PreprocessorError, e:
            # annotate exception with location in source file
            e.line = lineno
            e.filename = filename
            e.text = line
            self.log.info('error while processing "%s"' % (line.strip(),))
            raise
        except:
            self.log.info('error while processing "%s"' % (line.strip(),))
            raise
        else:
            self.log.info('done "%s"' % (filename),)


class Discard(object):
    """File like target object that consumes and discards all data"""
    def write(self, s):
        """dummy write"""


def main():
    import sys
    from optparse import OptionParser
    logging.basicConfig()

    parser = OptionParser()
    parser.add_option("-o", "--outfile",
                      dest = "outfile",
                      help = "name of the object file",
                      metavar = "FILE")
    parser.add_option("-p", "--preload",
                      dest = "preload",
                      help = "process this file first. its output is discarded but definitions are kept.",
                      metavar = "FILE")
    parser.add_option("-v", "--verbose",
                      action = "store_true",
                      dest = "verbose",
                      default = False,
                      help="print status messages")
    parser.add_option("--debug",
                      action = "store_true",
                      dest = "debug",
                      default = False,
                      help = "print debug messages to stdout")
    parser.add_option("-D", "--define",
                      action = "append",
                      dest = "defines",
                      metavar = "SYM[=VALUE]",
                      default = [],
                      help="define symbol")
    parser.add_option("-I", "--include-path",
                      action = "append",
                      dest = "include_paths",
                      metavar = "PATH",
                      default = [],
                      help="Add directory to the search path list for includes")

    (options, args) = parser.parse_args()

    if len(args) > 1:
        sys.stderr.write("Only one file at a time allowed.\n")
        sys.exit(1)

    if options.debug:
        logging.getLogger('cpp').setLevel(logging.DEBUG)
    elif options.verbose:
        logging.getLogger('cpp').setLevel(logging.INFO)
    else:
        logging.getLogger('cpp').setLevel(logging.WARN)


    if options.outfile:
        outfile = codecs.open(options.outfile, 'w', 'utf-8')
    else:
        outfile = codecs.getwriter("utf-8")(sys.stdout)

    if not args or args[0] == '-':
        infilename = '<stdin>'
        infile = codecs.getreader("utf-8")(sys.stdin)
    else:
        infilename = args[0]
        infile = codecs.open(infilename, 'r', 'utf-8')

    cpp = Preprocessor()
    # extend include search path
    cpp.include_path.extend(options.include_paths)
    # insert predefined symbols (XXX function like macros not yet supported)
    for definition in options.defines:
        if '=' in definition:
            symbol, value = definition.split('=', 1)
        else:
            symbol, value = definition, '1'
        cpp.namespace.defines[symbol] = value
    # process files now
    if options.preload:
        cpp.preprocess(open(options.preload), Discard(), options.preload)

    try:
        cpp.preprocess(infile, outfile, infilename)
    except PreprocessorError, e:
        sys.stderr.write('%s:%s: %s\n' % (e.filename, e.line, e))
        if options.debug:
            if hasattr(e, 'text'):
                sys.stderr.write('%s:%s: input line: %r\n' % (e.filename, e.line, e.text))
        sys.exit(1)


if __name__ == '__main__':
    main()
