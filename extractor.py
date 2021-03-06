import progressbar
import argparse
import pathlib
import logging
import mmap
import os
import sys
from io import BufferedReader

try:
	assert sys.version_info >= (3, 9, 5)
except AssertionError:
	print("Python 3.9.5 or greater is required", file=sys.stderr)
	exit(1)

try:
	progressbar.streams 
except AttributeError:
	print("progressbar is installed but progressbar2 required.")
	exit(1)
	
from DyldExtractor.extraction_context import ExtractionContext

from DyldExtractor.dyld.dyld_context import DyldContext
from DyldExtractor.dyld.dyld_structs import (
	dyld_cache_image_info
)

from DyldExtractor.macho.macho_context import MachOContext

from DyldExtractor.converter import (
	slide_info,
	macho_offset,
	linkedit_optimizer,
	stub_fixer,
	objc_fixer
)


def getArguments():
	"""Get program arguments.

	"""

	parser = argparse.ArgumentParser()
	parser.add_argument(
		"dyld_path",
		type=pathlib.Path,
		help="A path to the target DYLD cache."
	)
	parser.add_argument(
		"-e", "--extract",
		help="The name of the framework to extract. This can be longer for frameworks like UIKit, for example \"UIKit.framework/UIKit\""  # noqa
	)
	parser.add_argument(
		"-o", "--output",
		help="Specify the output path for the extracted framework. By default it extracts to the binaries folder."  # noqa
	)
	parser.add_argument(
		"-l", "--list-frameworks", action="store_true",
		help="List all frameworks in the cache."
	)
	parser.add_argument(
		"-f", "--filter",
		help="Filter out frameworks when listing them."
	)
	parser.add_argument(
		"-v", "--verbosity", type=int, choices=[0, 1, 2, 3], default=1,
		help="Increase verbosity, Option 1 is the default. | 0 = None | 1 = Critical Error and Warnings | 2 = 1 + Info | 3 = 2 + debug |"  # noqa
	)

	return parser.parse_args()


def _extractImage(
	dyldFile: BufferedReader,
	dyldCtx: DyldContext,
	image: dyld_cache_image_info,
	outputPath: str
) -> None:
	"""Extract an image and save it.

	The order of converters is essentally a reverse of Apple's SharedCacheBuilder
	"""

	logger = logging.getLogger()

	# get a a writable copy of the MachOContext
	machoFile = mmap.mmap(dyldFile.fileno(), 0, access=mmap.ACCESS_COPY)
	machoCtx = MachOContext(machoFile, dyldCtx.convertAddr(image.address))

	statusBar = progressbar.ProgressBar(
		prefix="{variables.unit} >> {variables.status} :: [",
		variables={"unit": "--", "status": "--"},
		widgets=[progressbar.widgets.AnimatedMarker(), "]"],
		redirect_stdout=True
	)

	extractionCtx = ExtractionContext(dyldCtx, machoCtx, statusBar, logger)

	slide_info.processSlideInfo(extractionCtx)
	linkedit_optimizer.optimizeLinkedit(extractionCtx)
	stub_fixer.fixStubs(extractionCtx)
	objc_fixer.fixObjC(extractionCtx)

	macho_offset.optimizeOffsets(extractionCtx)

	# Write the MachO file
	with open(outputPath, "wb") as outFile:
		statusBar.update(unit="Extractor", status="Writing file")

		newMachoCtx = extractionCtx.machoCtx

		# get the size of the file
		linkEditSeg = newMachoCtx.segments[b"__LINKEDIT"].seg
		fileSize = linkEditSeg.fileoff + linkEditSeg.filesize

		newMachoCtx.file.seek(0)
		outFile.write(newMachoCtx.file.read(fileSize))

	statusBar.update(unit="Extractor", status="Done")


def _filterImages(imagePaths: list[str], filterTerm: str):
	filteredPaths = []
	filterTerm = filterTerm.lower()

	for path in imagePaths:
		if filterTerm in path.lower():
			filteredPaths.append(path)

	return filteredPaths


def main():
	args = getArguments()

	# Configure Logging
	level = logging.WARNING  # default option

	if args.verbosity == 0:
		# Set the log level so high that it doesn't do anything
		level = 100
	elif args.verbosity == 2:
		level = logging.INFO
	elif args.verbosity == 3:
		level = logging.DEBUG

	progressbar.streams.wrap_stderr()  # needed for logging compatability

	logging.basicConfig(
		format="{asctime}:{msecs:3.0f} [{levelname:^9}] {filename}:{lineno:d} : {message}",  # noqa
		datefmt="%H:%M:%S",
		style="{",
		level=level
	)

	with open(args.dyld_path, "rb") as f:
		with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as dyldFile:
			dyldCtx = DyldContext(dyldFile)

			# enumerate images, create a map of paths and images
			imageMap = {}
			for imageData in dyldCtx.images:
				path = dyldCtx.readString(imageData.pathFileOffset)
				path = path[0:-1]  # remove null terminator
				path = path.decode("utf-8")

				imageMap[path] = imageData

			# list images option
			if args.list_frameworks:
				imagePaths = imageMap.keys()

				# filter if needed
				if args.filter:
					filterTerm = args.filter.strip().lower()
					imagePaths = _filterImages(imagePaths, filterTerm)

				print("Listing Images\n--------------")
				for path in imagePaths:
					print(path)

				return

			# extract image option
			if args.extract:
				extractionTarget = args.extract.strip()
				targetPaths = _filterImages(imageMap.keys(), extractionTarget)
				if len(targetPaths) == 0:
					print(f"Unable to find image \"{extractionTarget}\"")
					return

				outputPath = args.output
				if outputPath is None:
					outputPath = pathlib.Path("binaries/" + extractionTarget)
					os.makedirs(outputPath.parent, exist_ok=True)

				print(f"Extracting {targetPaths[0]}")
				_extractImage(f, dyldCtx, imageMap[targetPaths[0]], outputPath)
				return


if "__main__" == __name__:
	main()
